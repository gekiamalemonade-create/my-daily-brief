"""5. 音声化（Gemini 3.8 TTS の無料枠で2人の掛け合い → だめなら VOICEVOX）

Gemini 3.8 TTS は送った文章をそのまま読み上げる。話者と話し方は文章に書かず、
各パーツの speech_metadata（speaker / style）で指定する（Google の TTS ガイドの方式）。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import wave
from pathlib import Path

from google.genai import errors as genai_errors
from google.genai import types

from . import voicevox
from .gemini_llm import make_client

log = logging.getLogger("podcast")

RETRYABLE = {429, 500, 502, 503, 504}


def chunk_script(script: dict, max_chars: int) -> list[list[dict]]:
    """コーナーの切れ目で区切りつつ、1回の生成が max_chars を超えないようにまとめる"""
    pieces: list[list[dict]] = []
    for seg in script["segments"]:
        cur, n = [], 0
        for line in seg["lines"]:
            line = {**line, "_kind": seg["kind"]}
            if cur and n + len(line["text"]) > max_chars:
                pieces.append(cur)
                cur, n = [], 0
            cur.append(line)
            n += len(line["text"])
        if cur:
            pieces.append(cur)
    chunks: list[list[dict]] = []
    cur, n = [], 0
    for p in pieces:
        pn = sum(len(l["text"]) for l in p)
        if cur and n + pn > max_chars:
            chunks.append(cur)
            cur, n = [], 0
        cur = cur + p
        n += pn
    if cur:
        chunks.append(cur)
    return chunks


def synthesize(cfg, script: dict, workdir: Path) -> tuple[list[dict], str]:
    """音声を作り、(WAVの一覧, 使った方式 'gemini' / 'voicevox') を返す"""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    if cfg.tts.get("engine", "gemini") == "gemini":
        try:
            return _gemini(cfg, script, workdir), "gemini"
        except Exception as e:  # 無料枠の上限など
            if not cfg.tts.get("fallback_to_voicevox", True):
                raise
            log.warning("音声: Gemini で作れなかったので VOICEVOX に切り替えます（%s）", str(e)[:160])
    return voicevox.synthesize(cfg, script, workdir), "voicevox"


def _gemini(cfg, script: dict, workdir: Path) -> list[dict]:
    client = make_client()
    hosts = {h["id"]: h for h in cfg.hosts}
    speech_config = types.SpeechConfig(
        multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
            speaker_voice_configs=[
                types.SpeakerVoiceConfig(
                    speaker=h["id"],
                    voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=h["voice"])),
                )
                for h in cfg.hosts
            ]
        )
    )
    chunks = chunk_script(script, int(cfg.tts["max_chars_per_request"]))
    interval = float(os.environ.get("PODCAST_TTS_INTERVAL_SEC", cfg.tts.get("min_interval_sec", 21)))
    out = []
    for i, chunk in enumerate(chunks):
        if i:
            time.sleep(interval)  # 無料枠の「1分あたりの回数」に引っかからないように
        parts = [
            types.Part(text=l["text"], speech_metadata=types.SpeechMetadata(
                speaker=l["speaker"], style=(l.get("style") or hosts[l["speaker"]].get("style", ""))[:80]))
            for l in chunk
        ]
        log.info("音声(Gemini): %d/%d（%d文字）", i + 1, len(chunks), sum(len(l["text"]) for l in chunk))
        data, mime = _generate(client, cfg, parts, speech_config)
        path = workdir / f"chunk_{i:02d}.wav"
        save_audio(data, mime, path)
        out.append({"path": path, "first_kind": chunk[0]["_kind"], "last_kind": chunk[-1]["_kind"]})
    return out


def _generate(client, cfg, parts, speech_config) -> tuple[bytes, str]:
    models = [cfg.tts["model"]] + ([cfg.tts["fallback_model"]] if cfg.tts.get("fallback_model") else [])
    base_wait = float(os.environ.get("PODCAST_RETRY_BASE_SEC", "15"))
    last: Exception | None = None
    for model in models:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user", parts=parts)],
                    config=types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=speech_config),
                )
                data, mime = _audio_from(resp)
                if data:
                    return data, mime
                last = RuntimeError("音声データが空でした")
            except genai_errors.APIError as e:
                last = e
                code = getattr(e, "code", None)
                if code not in RETRYABLE or (code == 429 and "PerDay" in str(e)):
                    break  # 設定ミスや1日の上限は、待っても直らない
            except Exception as e:  # 通信の切断・タイムアウトなど
                last = e
            wait = base_wait * (attempt + 1)
            log.warning("  音声: %s で失敗（%s）。%.0f秒待って再試行", model, str(last)[:120], wait)
            time.sleep(wait)
        log.warning("  音声: %s をあきらめます", model)
    raise RuntimeError(f"Gemini の音声生成に失敗しました: {last}")


def _audio_from(resp) -> tuple[bytes, str]:
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            blob = getattr(part, "inline_data", None)
            if blob is not None and getattr(blob, "data", None):
                return blob.data, (blob.mime_type or "")
    return b"", ""


def save_audio(data: bytes, mime: str, path: Path) -> None:
    """Gemini 3.8 TTS は WAV（RIFFヘッダー付き）を返す。古いモデルの生PCMやその他の形式にも対応"""
    mime = (mime or "").lower()
    path = Path(path)
    if data[:4] == b"RIFF":
        path.write_bytes(data)
        return
    if "l16" in mime or "pcm" in mime:
        m = re.search(r"rate=(\d+)", mime)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(m.group(1)) if m else 24000)
            w.writeframes(data)
        return
    tmp = path.with_suffix(".bin")
    tmp.write_bytes(data)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp), "-ar", "24000", "-ac", "1", str(path)],
                   check=True)
    tmp.unlink(missing_ok=True)

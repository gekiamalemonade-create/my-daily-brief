"""VOICEVOX（無料の音声合成ソフト）で台本を読み上げる

- GitHub Actions では Docker で公式エンジン（voicevox/voicevox_engine）を起動して使う
- 生成した音声には「VOICEVOX:キャラクター名」のクレジット表記が必要（番組ページと RSS に自動で書く）
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

log = logging.getLogger("podcast")

LINE_PAUSE_SEC = 0.25


def _base() -> str:
    return os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021").rstrip("/")


def _call(method: str, path: str, params: dict | None = None, body: bytes | None = None, timeout: int = 120) -> bytes:
    url = _base() + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _alive() -> bool:
    try:
        _call("GET", "/version", timeout=5)
        return True
    except Exception:
        return False


def ensure_engine(cfg) -> None:
    if _alive():
        return
    image = cfg.tts.get("voicevox", {}).get("image", "voicevox/voicevox_engine:cpu-latest")
    if not shutil.which("docker"):
        raise RuntimeError("VOICEVOX エンジンが起動しておらず、Docker もありません")
    log.info("音声(VOICEVOX): エンジンを起動します（初回は数分かかることがあります）")
    subprocess.run(["docker", "run", "-d", "--rm", "-p", "127.0.0.1:50021:50021", image], check=True,
                   stdout=subprocess.DEVNULL)
    for _ in range(120):
        if _alive():
            return
        time.sleep(2)
    raise RuntimeError("VOICEVOX エンジンが起動しませんでした")


def style_ids(cfg) -> dict[str, int]:
    """各ホストに設定した話者名（例: 春日部つむぎ）から、スタイルID を探す"""
    want_style = cfg.tts.get("voicevox", {}).get("style", "ノーマル")
    speakers = json.loads(_call("GET", "/speakers"))
    out = {}
    for h in cfg.hosts:
        name = h.get("voicevox", "")
        sp = next((s for s in speakers if s.get("name") == name), None)
        if sp is None:
            raise RuntimeError(f"VOICEVOX に話者「{name}」が見つかりません（show.yaml の voicevox を確認）")
        styles = sp.get("styles", [])
        st = next((x for x in styles if x.get("name") == want_style), styles[0] if styles else None)
        out[h["id"]] = int(st["id"])
    return out


def credits(cfg) -> str:
    return " / ".join(f"VOICEVOX:{h['voicevox']}" for h in cfg.hosts if h.get("voicevox"))


def _synth_line(text: str, style: int, speed: float) -> bytes:
    query = json.loads(_call("POST", "/audio_query", {"text": text, "speaker": style}))
    query["speedScale"] = speed
    return _call("POST", "/synthesis", {"speaker": style}, json.dumps(query).encode("utf-8"), timeout=300)


def synthesize(cfg, script: dict, workdir: Path) -> list[dict]:
    """コーナーごとに1つの WAV を作る（行と行の間に短い間を入れる）"""
    ensure_engine(cfg)
    ids = style_ids(cfg)
    speed = float(cfg.tts.get("voicevox", {}).get("speed", 1.1))
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = []
    total = sum(len(s["lines"]) for s in script["segments"])
    done = 0
    for si, seg in enumerate(script["segments"]):
        path = workdir / f"vv_{si:02d}.wav"
        params = None
        frames = []
        for line in seg["lines"]:
            wav_bytes = _synth_line(line["text"], ids[line["speaker"]], speed)
            tmp = workdir / "_line.wav"
            tmp.write_bytes(wav_bytes)
            with wave.open(str(tmp), "rb") as w:
                p = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                if params is None:
                    params = p
                if p != params:
                    raise RuntimeError("VOICEVOX の音声形式が途中で変わりました")
                frames.append(w.readframes(w.getnframes()))
            frames.append(b"\x00" * int(params[2] * LINE_PAUSE_SEC) * params[0] * params[1])
            done += 1
        if params is None:
            continue
        with wave.open(str(path), "wb") as w:
            w.setnchannels(params[0])
            w.setsampwidth(params[1])
            w.setframerate(params[2])
            w.writeframes(b"".join(frames))
        out.append({"path": path, "first_kind": seg["kind"], "last_kind": seg["kind"]})
        log.info("音声(VOICEVOX): %d/%d 行", done, total)
    (workdir / "_line.wav").unlink(missing_ok=True)
    return out

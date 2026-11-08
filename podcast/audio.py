"""6. 音声のつなぎ合わせと MP3 化（ffmpeg）"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("podcast")

CHIME_BEFORE = {"deep", "skill", "takeaway"}  # このコーナーの前に短い効果音を入れる


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def duration_of(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                         check=True, capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def make_mp3(cfg, chunks: list[dict], out_mp3: Path, meta: dict) -> dict:
    out_mp3 = Path(out_mp3)
    work = out_mp3.parent / "_audio"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    fmt = ["-ar", "24000", "-ac", "1", "-sample_fmt", "s16"]

    gap = work / "gap.wav"
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(cfg.tts.get("gap_sec", 0.6)), *fmt, str(gap))
    chime = None
    if cfg.tts.get("chime", True):
        chime = work / "chime.wav"  # 自作の2音のチャイム（著作権の心配なし）
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=880:duration=0.14",
               "-f", "lavfi", "-i", "sine=frequency=1318.5:duration=0.32",
               "-filter_complex",
               "[0]volume=0.22,afade=t=out:st=0.08:d=0.06[a];[1]volume=0.22,afade=t=out:st=0.12:d=0.2[b];"
               "[a][b]concat=n=2:v=0:a=1", *fmt, str(chime))

    seq: list[Path] = []
    if chime:
        seq += [chime, gap]
    prev_kind = None
    for i, ch in enumerate(chunks):
        norm = work / f"n{i:02d}.wav"
        ffmpeg("-i", str(ch["path"]), *fmt, str(norm))
        if i:
            seq.append(gap)
            if chime and ch["first_kind"] != prev_kind and ch["first_kind"] in CHIME_BEFORE:
                seq += [chime, gap]
        seq.append(norm)
        prev_kind = ch["last_kind"]
    if chime:
        seq += [gap, chime]

    lst = work / "list.txt"
    lst.write_text("".join("file '{}'\n".format(str(p.resolve()).replace("'", "'\\''")) for p in seq), encoding="utf-8")
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst),
           "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "1",
           "-c:a", "libmp3lame", "-b:a", str(cfg.tts.get("mp3_bitrate", "64k")),
           "-metadata", f"title={meta['title']}", "-metadata", f"artist={meta['artist']}",
           "-metadata", f"album={meta['album']}", "-metadata", f"date={meta['date']}",
           str(out_mp3))
    shutil.rmtree(work, ignore_errors=True)
    info = {"duration": round(duration_of(out_mp3), 1), "bytes": out_mp3.stat().st_size}
    log.info("音声: MP3 完成 %.1f分 / %.1fMB", info["duration"] / 60, info["bytes"] / 1e6)
    return info

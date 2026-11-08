"""使い方: python -m podcast [--mock] [--site-dir site] [--date 2026-11-06] [--skip-tts]"""
from __future__ import annotations

import argparse
import logging
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m podcast", description="自分専用AIポッドキャストを1回分つくる")
    ap.add_argument("--site-dir", default="site", help="公開用ファイルの出力先（gh-pages の中身）")
    ap.add_argument("--out-dir", default="out", help="途中経過（JSON）の保存先")
    ap.add_argument("--root", default=None, help="config/ と state/ があるフォルダ（通常は指定不要）")
    ap.add_argument("--date", default=None, help="日付を指定して作る（例: 2026-11-06）")
    ap.add_argument("--mock", action="store_true", help="APIを使わずにテスト用のにせ応答で全工程を動かす（費用ゼロ）")
    ap.add_argument("--skip-tts", action="store_true", help="音声化をしない（台本まで）")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "google_genai", "google", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for h in logging.getLogger().handlers:  # SDKの「HTTP Request: ...」の行は出さない
        h.addFilter(lambda r: not str(r.getMessage()).startswith("HTTP Request:"))

    server = None
    if a.mock:
        from .mockserver import start
        server = start()
    try:
        from .pipeline import run
        run(root=a.root, site_dir=a.site_dir, out_dir=a.out_dir, date=a.date, skip_tts=a.skip_tts, mock=a.mock)
    finally:
        if server:
            server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""APIキーなし・ネット接続なしで（にせサーバーで）全工程を数日分まわして確かめるテスト（無料版）

使い方: python tests/test_e2e.py
- 本物の SDK・本物の RSS 解析（feedparser）・本物の本文抽出（trafilatura）を通す
- RSS の取得失敗・古い記事・重複・robots.txt・信頼リスト外の出典・429・VOICEVOX への切り替え、
  古い回の削除、同じ日の作り直し、評価の学習、リクエストの消化、ファクトチェックの反映を確認する
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check(cond: bool, msg: str) -> None:
    print(("  OK   " if cond else "  NG   ") + msg)
    if not cond:
        raise SystemExit(1)


def main() -> None:
    import logging
    logging.basicConfig(level=logging.WARNING)
    tmp = Path(tempfile.mkdtemp(prefix="podcast-free-test-"))
    work = tmp / "repo"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("out", "site", "__pycache__", ".git", "tests"))
    show = work / "config" / "show.yaml"
    show.write_text(show.read_text(encoding="utf-8").replace("keep_episodes: 21", "keep_episodes: 2"), encoding="utf-8")
    (work / "state" / "history.json").write_text("[]", encoding="utf-8")
    (work / "state" / "learned.json").write_text("{}", encoding="utf-8")

    from podcast import mockserver
    from podcast.pipeline import run

    server = mockserver.start()
    os.environ["FEEDBACK_URL"] = os.environ["GEMINI_BASE_URL"].rstrip("/") + "/feedback"
    os.environ["FEEDBACK_TOKEN"] = "test-token"
    site, out = tmp / "site", tmp / "out"
    try:
        for d in ["2026-11-06", "2026-11-07", "2026-11-08"]:
            run(root=work, site_dir=site, out_dir=out, date=d)
        os.environ["MOCK_TTS_ALWAYS_FAIL"] = "1"  # Gemini の音声が1日の上限切れ → VOICEVOX で作り直す
        run(root=work, site_dir=site, out_dir=out, date="2026-11-08")
    finally:
        os.environ.pop("MOCK_TTS_ALWAYS_FAIL", None)
        server.shutdown()

    print("\n結果の確認")
    c1 = json.loads((out / "2026-11-06" / "1_candidates.json").read_text(encoding="utf-8"))
    cands, feeds = c1["candidates"], c1["feeds"]
    check(feeds.get("金融庁 新着情報", "").startswith("取得できず"), "取得できない RSS はスキップしてログに残す")
    check(sum(v.endswith("件") for v in feeds.values()) == len(feeds) - 1, f"ほかの RSS（RSS2.0 / RDF / Atom）は読めた: {len(feeds) - 1} 本")
    check(not any("古い記事" in c["title"] for c in cands), "36時間より古い記事は候補から除外")
    check(sum("同じ記事" in c["title"] for c in cands) == 1, "複数の RSS に載った同じ記事は1件にまとめる")

    mats = json.loads((out / "2026-11-06" / "3_materials.json").read_text(encoding="utf-8"))
    robots = [h for h in mats["headlines"] if "/private/" in next(s["url"] for s in mats["sources"] if s["id"] == h["sid"])]
    check(len(robots) == 1 and not robots[0]["fetched"], "robots.txt で禁止のページは本文を取りに行かない")
    check(any(h["fetched"] and "12パーセント" in h["excerpt"] for h in mats["headlines"]), "記事ページから本文を取り出せた")
    deep = mats["deep"]
    check(deep["dropped"] == 1 and "100兆円" not in deep["notes"], "信頼リスト外（個人ブログ）が根拠の文は素材から除外")
    check(deep["notes"].count("[S") >= 3, "深掘りメモの各文に出典ID（きっかけ記事＋公的機関2件）")
    urls = [s["url"] for s in mats["sources"]]
    check(any("maff.go.jp/j/press/mock-deep-1" in u for u in urls), "検索の転送用URLから実際のページURLを取り出せた")
    maff = next(s for s in mats["sources"] if "maff.go.jp/j/press/mock-deep-1" in s["url"])
    check(maff["title"] == "テスト記事 mock-deep-1.html" and maff["publisher"] == "農林水産省",
          f"検索の出典名をドメイン名から記事の見出しに置き換え: {maff['publisher']}「{maff['title']}」")

    final = (out / "2026-11-06" / "5_script_final.json").read_text(encoding="utf-8")
    check("100兆円" not in final and "テスト修正" in final, "ファクトチェックの修正・削除が台本に反映")

    eps = json.loads((site / "episodes.json").read_text(encoding="utf-8"))
    check([e["id"] for e in eps] == ["20261108", "20261107"], "保存は新しい2本だけ（古い回は削除）")
    files = sorted(p.name for p in (site / "episodes").iterdir())
    check(len([f for f in files if f.endswith(".mp3")]) == 2, f"MP3 も2本（作り直しの古いMP3は消える）: {files}")
    usage = json.loads((out / "2026-11-08" / "usage.json").read_text(encoding="utf-8"))
    check(usage["audio_engine"] == "voicevox" and "VOICEVOX:春日部つむぎ" in eps[0]["credits"],
          "Gemini の音声が上限切れなら VOICEVOX で作り、クレジットを表記")
    feed = ET.parse(site / "feed.xml").getroot().find("channel")
    items = feed.findall("item")
    check(len(items) == 2 and "VOICEVOX:玄野武宏" in (items[0].findtext("description") or ""), "RSS は2本で、クレジットも載る")
    for it in items:
        enc = it.find("enclosure")
        name = enc.get("url").rsplit("/", 1)[-1]
        check(int(enc.get("length")) == (site / "episodes" / name).stat().st_size, f"RSS のファイルサイズが実物と一致: {name}")

    hist = json.loads((work / "state" / "history.json").read_text(encoding="utf-8"))
    check([h["date"] for h in hist] == ["2026-11-08", "2026-11-07", "2026-11-06"], "放送履歴が3日分（作り直しは上書き）")
    learned = json.loads((work / "state" / "learned.json").read_text(encoding="utf-8"))
    wd = learned["weight_delta"]
    check(wd.get("AI・テック", 0) > 0.4 and wd.get("世界情勢", 0) < -0.2, f"評価から重みを学習: {wd}")
    check(len(learned["processed_feedback"]) == 5, "同じ評価を二重に数えない")
    plan2 = json.loads((out / "2026-11-07" / "2_plan.json").read_text(encoding="utf-8"))
    check(len(learned["used_requests"]) == 1 and plan2["used_request_ids"] == [], "リクエストは1回使ったら消化済み")
    page = (site / "episodes" / "20261108.html").read_text(encoding="utf-8")
    check("費用 0円" in page and "data-fb" in page, "番組ページに費用0円の表示と評価ボタン")
    mp3 = site / "episodes" / next(f for f in files if f.endswith(".mp3"))
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(mp3)],
                               capture_output=True, text=True, check=True).stdout)
    check(dur > 2, f"MP3 が再生できる長さ: {dur:.1f}秒")
    print(f"\nすべて合格（作業フォルダ: {tmp}）")


if __name__ == "__main__":
    main()

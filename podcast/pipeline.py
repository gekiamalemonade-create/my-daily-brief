"""全体の流れ（無料版）: 評価の取り込み → RSS収集 → 選定 → 素材 → 台本 → 照合 → 音声 → 配信 → 記録"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from pathlib import Path

from . import config as config_mod
from . import feedback, voicevox
from .audio import make_mp3
from .editor import select
from .feeds import collect
from .gemini_llm import Gemini
from .publish import publish
from .research import gather
from .state import State
from .tts import synthesize
from .util import JST, now_jst, write_json
from .writer import factcheck, write_script

log = logging.getLogger("podcast")


def run(root=None, site_dir="site", out_dir="out", date: str | None = None, skip_tts: bool = False,
        mock: bool = False) -> dict:
    t0 = time.time()
    cfg = config_mod.load(root)
    now = now_jst()
    if date:
        now = dt.datetime.combine(dt.date.fromisoformat(date), now.time().replace(microsecond=0), JST)
    os.environ["PODCAST_NOW_ISO"] = now.isoformat()  # テスト用のにせサーバーが記事の日時を合わせるのに使う
    out = Path(out_dir) / now.date().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    state = State(cfg.root)
    llm = Gemini(cfg)
    log.info("==== %s %s の回をつくります（無料版）%s ====", cfg.show["title"], now.date(), "（テストモード）" if mock else "")

    rows = feedback.fetch_rows(os.environ.get("FEEDBACK_URL", ""), os.environ.get("FEEDBACK_TOKEN", ""),
                               (now.date() - dt.timedelta(days=21)).strftime("%Y%m%d"))
    fb = feedback.process(cfg, state, rows, now.date())

    candidates, feed_report = collect(cfg, now)
    write_json(out / "1_candidates.json", {"candidates": candidates, "feeds": feed_report})
    plan = select(cfg, llm, state, candidates, fb, now)
    write_json(out / "2_plan.json", plan)
    mats = gather(cfg, llm, plan, candidates, now)
    write_json(out / "3_materials.json", mats)
    script = write_script(cfg, llm, mats, state, now)
    write_json(out / "4_script_draft.json", script)
    script, report = factcheck(cfg, llm, script, mats)
    write_json(out / "5_script_final.json", script)
    write_json(out / "5_factcheck.json", report)
    if mock:
        script["title"] = "【テスト】" + script["title"]

    mp3, info, engine = None, {}, ""
    if not skip_tts:
        chunks, engine = synthesize(cfg, script, out / "tts")
        mp3 = out / "episode.mp3"
        info = make_mp3(cfg, chunks, mp3, {"title": script["title"], "artist": cfg.show.get("author", ""),
                                           "album": cfg.show["title"], "date": now.date().isoformat()})
    text_calls = len(llm.calls)
    usage = f"費用 0円（Gemini 無料枠：文章の生成 {text_calls} 回" + (f"／音声は {'Gemini' if engine == 'gemini' else 'VOICEVOX'}" if engine else "") + "）"
    ep = publish(cfg, site_dir, now, script, mats, report, mp3, info, usage_note=usage,
                 credits=voicevox.credits(cfg) if engine == "voicevox" else "")

    if not mock:  # テストの回は記録しない（好みの学習や重複チェックを汚さない）
        state.add_episode({"date": now.date().isoformat(), "title": script["title"],
                           "segments": [{"kind": s["kind"], "title": s["title"], "interest": s.get("interest", "")}
                                        for s in script["segments"] if s["kind"] in ("headline", "deep", "skill")]})
        state.add_skill(plan["skill"]["track"], plan["skill"]["title"])
        feedback.mark_used(state, plan.get("used_request_ids", []))
        state.save()
    write_json(out / "usage.json", {"calls": llm.calls, "audio_engine": engine, "audio_seconds": info.get("duration", 0)})
    log.info("==== 完成「%s」 %s ／ 所要 %.0f 秒 ====", script["title"], usage, time.time() - t0)
    return ep

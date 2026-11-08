"""4. 台本づくりと、出典との照合（ファクトチェック）"""
from __future__ import annotations

import datetime as dt
import logging
import re

from .gemini_llm import SYSTEM_CHECKER, SYSTEM_WRITER
from .util import render, truncate, weekday_ja

log = logging.getLogger("podcast")

KINDS = ["opening", "headline", "deep", "skill", "takeaway", "closing"]
KIND_LABEL = {"opening": "オープニング", "headline": "ヘッドライン", "deep": "深掘り", "skill": "1日1スキル",
              "takeaway": "示唆", "closing": "エンディング"}


def kind_label(kind: str, cfg=None) -> str:
    if kind == "takeaway" and cfg is not None:
        return f"{cfg.profile.get('name', 'あなた')}への示唆"
    return KIND_LABEL.get(kind, kind)


def script_schema(host_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "エピソードのタイトル（30文字以内）"},
            "summary": {"type": "string", "description": "概要欄に載せる要約（120文字以内）"},
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": KINDS},
                        "title": {"type": "string"},
                        "lines": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "speaker": {"type": "string", "enum": host_ids},
                                    "text": {"type": "string"},
                                    "style": {"type": "string"},
                                    "sources": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["speaker", "text", "sources"],
                            },
                        },
                    },
                    "required": ["kind", "title", "lines"],
                },
            },
        },
        "required": ["title", "summary", "segments"],
    }


FACTCHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["fix", "remove"]},
                    "fixed_text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["line_id", "verdict", "reason"],
            },
        },
        "summary": {"type": "string", "description": "照合の結果を1〜2文で"},
    },
    "required": ["issues", "summary"],
}

_URL = re.compile(r"https?://\S+")


def clean_text(text: str) -> str:
    """読み上げに向かない記号を取り除く"""
    text = _URL.sub("", text or "")
    text = re.sub(r"[*#`|>\[\]]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def format_materials(mats: dict) -> str:
    out = ["## 出典一覧（ID: 発信元「タイトル」 日付）"]
    out += [f"{s['id']}: {s['publisher']}「{truncate(s['title'], 80)}」 {s['date']}" for s in mats["sources"]]
    out.append("\n## ヘッドラインの素材（この順で1本ずつ segment を作る）")
    for i, h in enumerate(mats["headlines"], 1):
        block = [f"[{i}] 出典 {h['sid']}｜{h['interest']}｜{h['publisher']}｜{h['published'] or '日付不明'}",
                 f"見出し: {h['title']}", f"RSSの要約: {h['summary'] or 'なし'}"]
        if h["excerpt"]:
            block.append(f"記事の本文（抜粋）: {h['excerpt']}")
        else:
            block.append("記事の本文: （取得できなかった。見出しと要約の範囲だけで話し、細かい数字は言わない）")
        out.append("\n".join(block))
    d, k = mats["deep"], mats["skill"]
    out.append(f"\n## 深掘りの素材「{d['title']}」（観点: {d.get('angle') or '指定なし'}）\n{d['notes'] or '（調査メモなし）'}")
    out.append(f"\n## 1日1スキルの素材「{k['title']}」\n{k['notes'] or '（調査メモなし）'}")
    return "\n".join(out)


def _continuity(state, today: dt.date) -> str:
    rows = []
    for h in state.recent(7, today)[:5]:
        deep = next((s["title"] for s in h.get("segments", []) if s.get("kind") == "deep"), "")
        skill = next((s["title"] for s in h.get("segments", []) if s.get("kind") == "skill"), "")
        rows.append(f"- {h['date']}: 深掘り「{deep}」／スキル「{skill}」")
    return "\n".join(rows) or "（初回の放送）"


def write_script(cfg, llm, mats: dict, state, now: dt.datetime) -> dict:
    seg = cfg.show["segments"]
    minutes = int(cfg.show["episode_minutes"])
    target = minutes * int(cfg.show["chars_per_minute"])
    user = render(
        cfg.prompt("script"),
        today=now.date().isoformat(), weekday=weekday_ja(now.date()), show_title=cfg.show["title"],
        listener=cfg.profile.get("name", "リスナー"), goals=" / ".join(cfg.profile.get("goals", [])),
        hosts="\n".join(f"- id: {h['id']}｜名前: {h['name']}｜役割: {h['role']}" for h in cfg.hosts),
        minutes=str(minutes), target_chars=str(target), n_headlines=str(len(mats["headlines"])),
        deep_minutes=str(seg["deep_minutes"]), skill_minutes=str(seg["skill_minutes"]),
        ventures=cfg.ventures_text(), continuity=_continuity(state, now.date()), materials=format_materials(mats),
    )
    log.info("台本: 執筆中（目標 約%d文字）", target)
    data = llm.json("script", "script", user, script_schema(cfg.host_ids), SYSTEM_WRITER, max_output_tokens=32768)
    script = normalize_script(data, cfg, mats)
    log.info("台本: %d コーナー / %d 行 / %d 文字", len(script["segments"]),
             sum(len(s["lines"]) for s in script["segments"]), script["chars"])
    if script["chars"] < target * 0.5:
        log.warning("台本: 目標の半分未満の長さです（素材が少なかった可能性）")
    return script


def normalize_script(data: dict, cfg, mats: dict) -> dict:
    valid = {s["id"] for s in mats["sources"]}
    head_by_sid = {h["sid"]: h for h in mats["headlines"]}
    used_heads: set[str] = set()
    segs, n = [], 0
    for s in data.get("segments") or []:
        kind = s.get("kind") if s.get("kind") in KINDS else "headline"
        lines = []
        for ln in s.get("lines") or []:
            text = clean_text(str(ln.get("text", "")))
            if not text:
                continue
            n += 1
            speaker = ln.get("speaker") if ln.get("speaker") in cfg.host_ids else cfg.host_ids[0]
            lines.append({"id": f"L{n}", "speaker": speaker, "text": text, "style": str(ln.get("style") or "")[:60],
                          "sources": [x for x in (ln.get("sources") or []) if x in valid]})
        if not lines:
            continue
        seg = {"kind": kind, "title": str(s.get("title") or kind_label(kind, cfg)), "lines": lines}
        if kind == "headline":
            # 行に付いた出典IDから、どのヘッドライン素材のコーナーかを特定する（評価の学習に使うため）
            h = next((head_by_sid[x] for l in lines for x in l["sources"] if x in head_by_sid and x not in used_heads), None)
            if h is None:
                h = next((x for x in mats["headlines"] if x["sid"] not in used_heads), None)
            if h:
                used_heads.add(h["sid"])
            seg["interest"] = h["interest"] if h else "その他"
            seg["main_source"] = h["sid"] if h else ""
        elif kind == "deep":
            seg["interest"] = mats["deep"].get("interest", "その他")
        elif kind == "skill":
            seg["interest"] = "スキル"
            seg["track"] = mats["skill"].get("track", "")
        else:
            seg["interest"] = ""
        segs.append(seg)
    if not segs:
        raise RuntimeError("台本が空でした")
    return {"title": str(data.get("title") or "今日のブリーフ")[:60], "summary": str(data.get("summary") or "")[:300],
            "segments": segs, "chars": sum(len(l["text"]) for s in segs for l in s["lines"])}


def _evidence_text(mats: dict) -> str:
    head_by_sid = {h["sid"]: h for h in mats["headlines"]}
    blocks = []
    for s in mats["sources"]:
        b = [f"### {s['id']} {s['publisher']}「{truncate(s['title'], 80)}」 {s['date']}"]
        h = head_by_sid.get(s["id"])
        if h and h["summary"]:
            b.append("RSSの要約: " + h["summary"])
        if s["cited"]:
            b.append("根拠になった文・要約: " + " / ".join(truncate(c, 300) for c in s["cited"][:6]))
        if s["excerpt"]:
            b.append("本文抜粋: " + truncate(s["excerpt"], 2500))
        blocks.append("\n".join(b))
    return "\n\n".join(blocks) or "（証拠なし）"


def factcheck(cfg, llm, script: dict, mats: dict) -> tuple[dict, dict]:
    names = {h["id"]: h["name"] for h in cfg.hosts}
    lines_text = "\n".join(
        f"{l['id']}｜{s['kind']}｜{names[l['speaker']]}｜{l['text']}｜{','.join(l['sources']) or 'なし'}"
        for s in script["segments"] for l in s["lines"])
    user = render(cfg.prompt("factcheck"), evidence=_evidence_text(mats), script_lines=lines_text,
                  listener=cfg.profile.get("name", "リスナー"), ventures=cfg.ventures_text())
    log.info("照合: 台本 %d 行を出典と照合", sum(len(s["lines"]) for s in script["segments"]))
    res = llm.json("factcheck", "factcheck", user, FACTCHECK_SCHEMA, SYSTEM_CHECKER, max_output_tokens=16384)

    index = {l["id"]: (s, l) for s in script["segments"] for l in s["lines"]}
    total = len(index)
    fixed = removed = 0
    applied = []
    for it in res.get("issues") or []:
        lid = str(it.get("line_id", "")).strip()
        if lid not in index:
            continue
        seg, line = index.pop(lid)
        if it.get("verdict") == "fix" and clean_text(str(it.get("fixed_text") or "")):
            applied.append({"line_id": lid, "verdict": "fix", "before": line["text"],
                            "after": clean_text(it["fixed_text"]), "reason": it.get("reason", "")})
            line["text"] = clean_text(it["fixed_text"])
            line["checked"] = "fixed"
            fixed += 1
        elif it.get("verdict") == "remove":
            applied.append({"line_id": lid, "verdict": "remove", "before": line["text"], "reason": it.get("reason", "")})
            seg["lines"].remove(line)
            removed += 1
    script["segments"] = [s for s in script["segments"] if s["lines"]]
    script["chars"] = sum(len(l["text"]) for s in script["segments"] for l in s["lines"])
    if total and removed / total > 0.4:
        log.warning("照合: 4割以上の行が削除されました。素材が不足していた可能性があります")
    report = {"checked_lines": total, "fixed": fixed, "removed": removed,
              "summary": str(res.get("summary", "")), "issues": applied}
    log.info("照合: 修正 %d 件 / 削除 %d 件", fixed, removed)
    return script, report

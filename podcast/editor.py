"""2. 今日の構成を決める（好みの重み × 最近の評価 × 過去に扱ったテーマ × リクエスト）"""
from __future__ import annotations

import datetime as dt
import logging

from .gemini_llm import SYSTEM_EDITOR
from .util import clamp, render, weekday_ja

log = logging.getLogger("podcast")


def effective_weights(cfg, state) -> dict[str, float]:
    delta = state.learned.get("weight_delta", {})
    return {i["id"]: round(clamp(float(i["weight"]) + float(delta.get(i["id"], 0)), 0.5, 5.0), 2)
            for i in cfg.profile["interests"]}


def selection_schema(cfg) -> dict:
    tracks = cfg.profile.get("learning_tracks", [])
    interests = cfg.interest_ids + ["その他"]
    return {
        "type": "object",
        "properties": {
            "headlines": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"candidate_id": {"type": "string"},
                                         "interest": {"type": "string", "enum": interests},
                                         "reason": {"type": "string"}},
                          "required": ["candidate_id", "interest"]},
            },
            "deep_dive": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "interest": {"type": "string", "enum": interests},
                    "angle": {"type": "string"},
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "seed_candidate_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "interest", "questions"],
            },
            "skill": {
                "type": "object",
                "properties": {"track": {"type": "string", "enum": tracks} if tracks else {"type": "string"},
                               "title": {"type": "string"},
                               "questions": {"type": "array", "items": {"type": "string"}}},
                "required": ["track", "title"],
            },
            "used_request_ids": {"type": "array", "items": {"type": "string"}},
            "editor_note": {"type": "string"},
        },
        "required": ["headlines", "deep_dive", "skill"],
    }


def _history_text(state, today: dt.date) -> str:
    rows = []
    for h in state.recent(14, today):
        segs = [f"{s['kind']}「{s['title']}」" for s in h.get("segments", []) if s.get("kind") in ("headline", "deep", "skill")]
        rows.append(f"- {h['date']}: " + " / ".join(segs))
    return "\n".join(rows) or "（まだなし）"


def select(cfg, llm, state, candidates: list[dict], fb: dict, now: dt.datetime) -> dict:
    today = now.date()
    n = min(int(cfg.show["segments"]["headlines"]), len(candidates))
    tracks = cfg.profile.get("learning_tracks", [])
    progress = state.learned.get("skill_progress", {})
    weights = effective_weights(cfg, state)
    cand_text = "\n".join(
        f"{c['id']} [{c['source']}｜{c['published'] or '日付不明'}｜関係しやすい分野: {'・'.join(c['interests']) or 'なし'}] "
        f"{c['title']} — {c['summary']}" for c in candidates) or "（候補なし。ヘッドラインは0本でよい）"
    prompt = render(
        cfg.prompt("select"),
        today=today.isoformat(), weekday=weekday_ja(today), profile=cfg.profile_text(),
        weights="\n".join(f"- {k}: {v}" for k, v in weights.items()),
        feedback=fb["summary_text"], requests=fb["requests_text"],
        history=_history_text(state, today),
        skill_progress="\n".join(f"- {t}: " + ("、".join(progress.get(t, [])[-5:]) or "（まだなし）") for t in tracks) or "（トラックなし）",
        candidates=cand_text, n_headlines=str(n), explore_ratio=str(cfg.profile.get("explore_ratio", 0.2)),
    )
    sel = llm.json("select", "editor", prompt, selection_schema(cfg), SYSTEM_EDITOR, max_output_tokens=8192)
    plan = _validate(sel, candidates, n, cfg, fb)
    log.info("選定: ヘッドライン%d本 / 深掘り「%s」 / スキル「%s」", len(plan["headlines"]), plan["deep_dive"]["title"], plan["skill"]["title"])
    return plan


def _validate(sel: dict, candidates: list[dict], n: int, cfg, fb: dict) -> dict:
    by_id = {c["id"]: c for c in candidates}
    ids = set(cfg.interest_ids)

    def interest_of(cid: str, proposed) -> str:
        if proposed in ids:
            return proposed
        hints = [x for x in by_id[cid]["interests"] if x in ids]
        return hints[0] if hints else "その他"

    heads, used = [], set()
    for h in sel.get("headlines") or []:
        cid = str((h or {}).get("candidate_id", "")).strip()
        if cid in by_id and cid not in used:
            heads.append({"candidate_id": cid, "interest": interest_of(cid, h.get("interest")), "reason": h.get("reason", "")})
            used.add(cid)
    for c in candidates:  # 足りなければ候補の上から補う
        if len(heads) >= n:
            break
        if c["id"] not in used:
            heads.append({"candidate_id": c["id"], "interest": interest_of(c["id"], None), "reason": "自動で補充"})
            used.add(c["id"])
    heads = heads[:n]

    deep = dict(sel.get("deep_dive") or {})
    deep["seed_candidate_ids"] = [x for x in deep.get("seed_candidate_ids") or [] if x in by_id][:2]
    deep["questions"] = [q for q in deep.get("questions") or [] if isinstance(q, str)][:4]
    deep.setdefault("angle", "")
    if deep.get("interest") not in ids:
        deep["interest"] = "その他"
    if not deep.get("title"):
        deep["title"] = by_id[heads[0]["candidate_id"]]["title"] if heads else "今日の注目テーマ"

    tracks = cfg.profile.get("learning_tracks", [])
    skill = dict(sel.get("skill") or {})
    if tracks and skill.get("track") not in tracks:
        skill["track"] = tracks[0]
    skill.setdefault("track", "教養")
    skill["title"] = skill.get("title") or skill["track"]
    skill["questions"] = [q for q in skill.get("questions") or [] if isinstance(q, str)][:3]

    valid_req = {r["id"] for r in fb.get("requests", [])}
    return {"headlines": heads, "deep_dive": deep, "skill": skill,
            "used_request_ids": [r for r in sel.get("used_request_ids") or [] if r in valid_req],
            "editor_note": sel.get("editor_note", "")}

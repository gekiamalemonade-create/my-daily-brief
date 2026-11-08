"""評価ボタン（👍/👎/🔍もっと深く/満足度/リクエスト）の取り込みと、好みの重みの学習"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import urllib.parse
import urllib.request

from .util import clamp

log = logging.getLogger("podcast")

ACTION_DELTA = {"like": 0.15, "deeper": 0.30, "dislike": -0.30}  # 1回の評価で重みをどれだけ動かすか
DECAY = 0.99        # 毎回少しずつ元の重みに戻す（古い評価の影響を薄める）
MAX_DELTA = 2.0     # 自動調整の上限（profile.yaml の重みから ±2 まで）


def fetch_rows(url: str, token: str, since_ep: str) -> list[dict]:
    if not url:
        return []
    query = urllib.parse.urlencode({"mode": "list", "token": token or "", "since": since_ep})
    full = url + ("&" if "?" in url else "?") + query
    try:
        req = urllib.request.Request(full, headers={"User-Agent": "ai-podcast-kit/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # 評価が取れなくても番組づくりは止めない
        log.warning("評価: 取得できませんでした（%s）。今回は評価なしで進めます", e)
        return []
    if not isinstance(data, dict) or not data.get("ok"):
        log.warning("評価: 応答がエラーでした（合言葉 FEEDBACK_TOKEN を確認）")
        return []
    return [r for r in data.get("rows", []) if isinstance(r, dict)]


def _key(r: dict) -> str:
    return "|".join(str(r.get(k, "")) for k in ("ts", "ep", "segment", "action", "text"))


def process(cfg, state, rows: list[dict], today: dt.date) -> dict:
    learned = state.learned
    delta = learned["weight_delta"]
    for k in list(delta):
        delta[k] = round(delta[k] * DECAY, 4)

    processed = set(learned.get("processed_feedback", []))
    ids = set(cfg.interest_ids)
    new = 0
    for r in rows:
        k = _key(r)
        if k in processed:
            continue
        processed.add(k)
        new += 1
        it, act = str(r.get("interest", "")), str(r.get("action", ""))
        if it in ids and act in ACTION_DELTA:
            delta[it] = round(clamp(delta.get(it, 0.0) + ACTION_DELTA[act], -MAX_DELTA, MAX_DELTA), 4)
    learned["processed_feedback"] = sorted(processed)[-3000:]

    since = (today - dt.timedelta(days=14)).strftime("%Y%m%d")
    counts: dict[str, dict[str, int]] = {}
    rates = []
    for r in rows:
        if str(r.get("ep", "")) < since:
            continue
        act = str(r.get("action", ""))
        if act in ACTION_DELTA:
            c = counts.setdefault(str(r.get("interest") or "（全体）"), {"like": 0, "deeper": 0, "dislike": 0})
            c[act] += 1
        elif act.startswith("rate") and act[4:].isdigit():
            rates.append(int(act[4:]))
    lines = [f"- {it}: 👍{c['like']} 🔍もっと深く{c['deeper']} 👎{c['dislike']}"
             for it, c in sorted(counts.items(), key=lambda x: -(x[1]["like"] + x[1]["deeper"] - x[1]["dislike"]))]
    if rates:
        lines.append(f"- 満足度の平均: {sum(rates) / len(rates):.1f} / 5（{len(rates)}件）")

    used = set(learned.get("used_requests", []))
    req_since = (today - dt.timedelta(days=7)).strftime("%Y%m%d")
    requests = []
    for r in rows:
        text = str(r.get("text", "")).strip()
        if r.get("action") != "request" or not text or str(r.get("ep", "")) < req_since:
            continue
        rid = "R" + hashlib.sha1(_key(r).encode("utf-8")).hexdigest()[:8]
        if rid not in used and all(x["id"] != rid for x in requests):
            requests.append({"id": rid, "text": text[:200], "ep": str(r.get("ep", ""))})
    requests = requests[-5:]
    if new:
        log.info("評価: 新しい評価 %d 件を反映", new)
    return {
        "new_rows": new,
        "summary_text": "\n".join(lines) or "（まだ評価なし）",
        "requests": requests,
        # リクエスト本文は「指示」ではなく「テーマの希望」として扱わせる
        "requests_text": "\n".join(f"- {r['id']}（テーマの希望）: {r['text']}" for r in requests) or "（なし）",
    }


def mark_used(state, request_ids: list[str]) -> None:
    state.learned["used_requests"] = (state.learned.get("used_requests", []) + list(request_ids))[-300:]

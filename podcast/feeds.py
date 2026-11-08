"""1. ニュース候補の収集（公式の RSS から。検索は使わないので無料・情報源がはっきりしている）"""
from __future__ import annotations

import calendar
import datetime as dt
import html
import logging
import re

import feedparser

from . import fetcher
from .util import JST, normalize_url, truncate

log = logging.getLogger("podcast")

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _when(entry) -> dt.datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    try:
        return dt.datetime.fromtimestamp(calendar.timegm(t), tz=dt.timezone.utc).astimezone(JST)
    except (OverflowError, ValueError, TypeError):
        return None


def collect(cfg, now: dt.datetime) -> tuple[list[dict], dict]:
    """候補の一覧と、RSS ごとの取得結果（ログ用）を返す"""
    hours = int(cfg.show.get("lookback_hours", 36))
    since = now - dt.timedelta(hours=hours)
    per_feed = int(cfg.collect.get("max_items_per_feed", 8))
    report: dict[str, str] = {}
    by_feed: list[list[dict]] = []

    for f in cfg.feeds:
        status, body, _, _ = fetcher.get(f["url"])
        if status != 200 or not body:
            report[f["name"]] = f"取得できず（HTTP {status}）"
            continue
        d = feedparser.parse(body)
        if not d.entries:
            report[f["name"]] = "記事が0件（RSS の形式を読めなかった可能性）"
            continue
        items = []
        for i, e in enumerate(d.entries[: per_feed * 3]):
            link = (e.get("link") or "").strip()
            title = _clean(e.get("title", ""))
            if not link or not title:
                continue
            when = _when(e)
            if when and when < since:
                continue
            if not when and i >= 3:  # 日付がない記事は、先頭の数件（新しいもの）だけ使う
                continue
            items.append({
                "title": truncate(title, 120),
                "url": link,
                "source": f["name"],
                "published": when.strftime("%Y-%m-%d %H:%M") if when else "",
                "summary": truncate(_clean(e.get("summary") or e.get("description") or ""), 240),
                "interests": list(f.get("interests", [])),
            })
            if len(items) >= per_feed:
                break
        report[f["name"]] = f"{len(items)}件"
        by_feed.append(items)

    # RSS ごとに交互に並べて、特定の RSS ばかりにならないようにする（重複は除く）
    seen_url, seen_title, merged = set(), set(), []
    for rank in range(per_feed):
        for items in by_feed:
            if rank < len(items):
                c = items[rank]
                n, t = normalize_url(c["url"]), re.sub(r"\s", "", c["title"])
                if n in seen_url or t in seen_title:
                    continue
                seen_url.add(n)
                seen_title.add(t)
                merged.append(c)
    merged = merged[: int(cfg.collect.get("max_candidates", 70))]
    for i, c in enumerate(merged, 1):
        c["id"] = f"C{i}"
    ok = sum(1 for v in report.values() if v.endswith("件"))
    log.info("収集: RSS %d/%d 本から候補 %d 件", ok, len(cfg.feeds), len(merged))
    for name, r in report.items():
        if not r.endswith("件"):
            log.warning("  RSS「%s」: %s", name, r)
    return merged, report

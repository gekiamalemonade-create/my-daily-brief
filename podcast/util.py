"""小さな共通関数"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
WEEKDAYS_JA = "月火水木金土日"


def now_jst() -> dt.datetime:
    return dt.datetime.now(JST)


def weekday_ja(d: dt.date) -> str:
    return WEEKDAYS_JA[d.weekday()]


def render(template: str, **values) -> str:
    """プロンプト内の {{name}} を置き換える（JSONの { } と衝突しないように二重かっこを使う）"""

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"プロンプトの差し込み値がありません: {key}")
        v = values[key]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)

    return re.sub(r"\{\{(\w+)\}\}", repl, template)


def extract_json(text: str):
    """文章の中から JSON を取り出す。最後の ```json ブロックを優先。末尾カンマ程度は直す"""
    if not text:
        return None
    candidates = list(reversed(re.findall(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, flags=re.S)))
    if not candidates:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            candidates = [text[s : e + 1]]
    for c in candidates:
        for attempt in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    return None


def normalize_url(url: str) -> str:
    """比較用にURLを正規化（スキーム・ホストを小文字、末尾スラッシュ・#以降・utm_系を除去）"""
    if not url:
        return ""
    url = url.strip().strip("<>").strip()
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")])
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, query, ""))


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def domain_allowed(url: str, domains: list[str]) -> bool:
    """allowed_domains と同じ考え方（サブドメインを含む・パス指定にも対応）で判定"""
    host = host_of(url)
    path = urlsplit(url).path if url else ""
    for d in domains:
        d = d.lower().strip()
        dom, _, dpath = d.partition("/")
        if host == dom or host.endswith("." + dom):
            if not dpath or path.lstrip("/").startswith(dpath.rstrip("*").rstrip("/")):
                return True
    return False


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"

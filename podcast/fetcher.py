"""ネットからの取得（RSS・記事ページ・リダイレクト先の確認）

- 記事ページは robots.txt を確認し、禁止されていれば取りに行かない
- 同じサイトへは間隔をあけてアクセスする
- テスト時（環境変数 PODCAST_MOCK_HTTP）は、すべてのアクセスをにせサーバー経由にする
"""
from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

import trafilatura
from trafilatura.utils import decode_file

from .util import host_of

log = logging.getLogger("podcast")

BOT = "personal-podcast-bot"
UA = f"Mozilla/5.0 (compatible; {BOT}/1.0; personal listening)"
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}
SAME_HOST_INTERVAL = 0.7


def _route(url: str) -> str:
    mock = os.environ.get("PODCAST_MOCK_HTTP", "").strip()
    return f"{mock}/proxy?url={urllib.parse.quote(url, safe='')}" if mock else url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def get(url: str, timeout: int = 20, follow: bool = True) -> tuple[int, bytes, dict, str]:
    """(ステータス, 本文, ヘッダー, 最終URL) を返す。通信エラーは status=0"""
    host = host_of(url)
    wait = SAME_HOST_INTERVAL - (time.time() - _last_hit.get(host, 0))
    if wait > 0 and not os.environ.get("PODCAST_MOCK_HTTP"):
        time.sleep(wait)
    _last_hit[host] = time.time()
    req = urllib.request.Request(_route(url), headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            final = url if os.environ.get("PODCAST_MOCK_HTTP") else r.geturl()
            return r.status, r.read(8_000_000), dict(r.headers), final
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return e.code, body, dict(e.headers or {}), url
    except Exception as e:  # タイムアウト・名前解決エラーなど
        log.info("  取得できませんでした: %s（%s）", url, e)
        return 0, b"", {}, url


def robots_allowed(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        status, body, _, _ = get(base + "/robots.txt", timeout=10)
        if status in (401, 403):
            rp = urllib.robotparser.RobotFileParser()
            rp.disallow_all = True
        elif 200 <= status < 300:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(body.decode("utf-8", "replace").splitlines())
        else:  # robots.txt が無い → 制限なし
            rp = None
        _robots[base] = rp
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(BOT, url)


def resolve_redirect(url: str, max_hops: int = 4) -> str:
    """Google 検索の出典リンク（転送用URL）から、実際の記事URLを取り出す"""
    for _ in range(max_hops):
        status, _, headers, _ = get(url, timeout=15, follow=False)
        loc = headers.get("Location") or headers.get("location")
        if status in (301, 302, 303, 307, 308) and loc:
            url = urllib.parse.urljoin(url, loc)
            continue
        return url
    return url


def fetch_article(url: str, max_chars: int = 3000) -> dict:
    """記事ページを取得して本文を取り出す。{"ok", "url", "title", "text", "reason"}"""
    if not robots_allowed(url):
        return {"ok": False, "url": url, "title": "", "text": "", "reason": "robots.txt で禁止されているため取得しない"}
    status, body, headers, final = get(url)
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    if status != 200 or not body:
        return {"ok": False, "url": url, "title": "", "text": "", "reason": f"HTTP {status}"}
    if "pdf" in ctype or body[:5] == b"%PDF-":
        return {"ok": False, "url": final, "title": "", "text": "", "reason": "PDF のため本文を取り出さない"}
    html = decode_file(body)
    text = trafilatura.extract(html, url=final, include_comments=False, include_tables=True, favor_precision=True) or ""
    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        title = (meta.title or "") if meta else ""
    except Exception:
        pass
    text = text.strip()
    return {"ok": len(text) >= 80, "url": final, "title": title, "text": text[:max_chars],
            "reason": "" if len(text) >= 80 else "本文を取り出せなかった"}

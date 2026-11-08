"""テスト用のにせサーバー（--mock で使う。APIキー不要・ネット接続不要）

本物の SDK・本物の RSS 解析・本物の本文抽出を通して、次のものの応答を再現する:
- RSS（RSS 2.0 / RSS 1.0(RDF) / Atom）と記事ページ、robots.txt、検索の転送用URL
- Gemini API（選定・台本・照合の JSON、検索つき調査、音声）
- VOICEVOX エンジン、評価シート（Apps Script）
わざと「取得できない RSS」「古い記事」「重複記事」「robots.txt で禁止のページ」「信頼リスト外の出典」
「429 エラー」「1日の上限切れ（→VOICEVOX へ切り替え）」を混ぜて、守りの処理が働くかも確かめる。
"""
from __future__ import annotations

import array
import base64
import datetime as dt
import io
import json
import math
import os
import re
import threading
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
COUNTER = {"tts": 0}

REDIRECTS = {
    "maff-1": "https://www.maff.go.jp/j/press/mock-deep-1.html",
    "meti-2": "https://www.meti.go.jp/press/mock-deep-2.html",
    "blog-3": "https://example-blog.com/post-3",
}


# ---------- 共通 ----------
def _wav(seconds: float, rate: int = 24000) -> bytes:
    n = max(1, int(seconds * rate))
    samples = array.array("h", (int(2500 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(n)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def _now() -> dt.datetime:
    iso = os.environ.get("PODCAST_NOW_ISO")
    return dt.datetime.fromisoformat(iso) if iso else dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))


# ---------- RSS・記事ページ ----------
def _feed(url: str) -> tuple[int, str, str]:
    if "fsaNewsListAll" in url:  # わざと取得できない RSS
        return 404, "text/plain", "not found"
    p = urllib.parse.urlsplit(url)
    host = p.netloc
    key = re.sub(r"\W", "", p.path)[-12:]
    now = _now()
    items = []
    for i in range(3):
        items.append((f"{host} のテスト記事{i}（{key}）", f"https://{host}/mock/{key}-{i}.html", now - dt.timedelta(hours=1 + i),
                      f"{host} が新しい方針を発表しました。テスト用の要約{i}です。"))
    items.append(("5日前の古い記事（除外されるはず）", f"https://{host}/mock/{key}-old.html", now - dt.timedelta(days=5), "古い"))
    if "cat0" in url or "cat5" in url:  # NHK の主要と経済に同じ記事（重複の除外を確認）
        items.append(("両方の RSS に載る同じ記事", "https://www3.nhk.or.jp/news/html/mock-shared.html", now - dt.timedelta(hours=2),
                      "同じ記事です。"))
    if "cat0" in url:  # robots.txt で禁止されたページ（本文を取りに行かない）
        items.insert(0, ("robots.txt で禁止のページ", "https://www3.nhk.or.jp/private/mock-robots.html", now - dt.timedelta(minutes=30),
                         "禁止ページの要約です。"))
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    if url.endswith(".rdf"):
        body = ('<?xml version="1.0" encoding="UTF-8"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>RDF</title></channel>'
                + "".join(f'<item rdf:about="{esc(l)}"><title>{esc(t)}</title><link>{esc(l)}</link><description>{esc(d)}</description>'
                          f'<dc:date>{w.isoformat()}</dc:date></item>' for t, l, w, d in items) + "</rdf:RDF>")
        return 200, "application/rdf+xml", body
    if "atom" in url:
        body = ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>'
                + "".join(f'<entry><title>{esc(t)}</title><link href="{esc(l)}"/><id>{esc(l)}</id><updated>{w.isoformat()}</updated>'
                          f'<summary>{esc(d)}</summary></entry>' for t, l, w, d in items) + "</feed>")
        return 200, "application/atom+xml", body
    from email.utils import format_datetime
    body = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>RSS</title>'
            + "".join(f"<item><title>{esc(t)}</title><link>{esc(l)}</link><description>{esc(d)}</description>"
                      f"<pubDate>{format_datetime(w)}</pubDate></item>" for t, l, w, d in items) + "</channel></rss>")
    return 200, "application/rss+xml", body


def _article(url: str) -> tuple[int, str, str]:
    title = "テスト記事 " + urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    paras = [
        "11月5日、担当の省庁は新しい支援策を発表しました。対象は約3万社で、制度は2026年4月に始まりました。",
        "発表によりますと、売上は前年比12パーセント増えました。担当者は「来年度から本格的に広げたい」と話しています。",
        "専門家は、中小企業が価格の根拠を数字で示すことが大切だと指摘しています。",
    ]
    body = (f"<html><head><meta charset='utf-8'><title>{title}</title></head><body><header><a href='/'>トップ</a> メニュー</header>"
            f"<article><h1>{title}</h1>" + "".join(f"<p>{p}</p>" for p in paras) + "</article>"
            "<footer>Copyright テスト</footer></body></html>")
    return 200, "text/html; charset=utf-8", body


def handle_proxy(url: str) -> tuple[int, dict, bytes]:
    if url.endswith("/robots.txt"):
        return 200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /private/\n"
    m = re.search(r"grounding-api-redirect/([\w-]+)", url)
    if m:
        return 302, {"Location": REDIRECTS.get(m.group(1), "https://example.com/")}, b""
    if url.endswith(".html"):
        code, ctype, body = _article(url)
    else:
        code, ctype, body = _feed(url)
    return code, {"Content-Type": ctype}, body.encode("utf-8")


# ---------- Gemini ----------
def _k(d, *names):
    for n in names:
        if isinstance(d, dict) and n in d:
            return d[n]
    return None


def _prompt(body: dict) -> str:
    parts = (_k(body, "contents") or [{}])[0].get("parts", [])
    return " ".join(p.get("text", "") for p in parts if isinstance(p, dict))


def _text_resp(text: str, model: str = "mock", grounding: dict | None = None) -> dict:
    cand = {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP", "index": 0}
    if grounding:
        cand["groundingMetadata"] = grounding
    return {"candidates": [cand], "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 400, "totalTokenCount": 1600},
            "modelVersion": model}


def _plan(text: str) -> dict:
    cands = re.findall(r"^(C\d+) \[.*?関係しやすい分野: (.*?)\]", text, flags=re.M)
    m = re.search(r"「1日1スキル」の進み具合.*?\n- (.+?): ", text, flags=re.S)
    reqs = re.findall(r"^- (R[0-9a-f]{8})", text, flags=re.M)
    heads = [{"candidate_id": c, "interest": (h.split("・")[0] if h and h != "なし" else "その他"), "reason": "テスト"}
             for c, h in cands[:4]]
    return {"headlines": heads,
            "deep_dive": {"title": "テスト深掘り：取引の適正化の仕組み", "interest": "中小企業・価格転嫁", "angle": "仕組みと影響",
                          "questions": ["いつ始まったか", "対象は誰か", "何が変わるか"], "seed_candidate_ids": [c for c, _ in cands[:1]]},
            "skill": {"track": m.group(1) if m else "教養", "title": "テストスキル：STPの基本", "questions": ["STPとは"]},
            "used_request_ids": reqs[:1], "editor_note": "テスト用の構成です。"}


def _script(text: str) -> dict:
    hosts = re.findall(r"^- id: (\w+)｜", text, flags=re.M) or ["A", "B"]
    heads = re.findall(r"^\[(\d+)\] 出典 (S\d+)", text, flags=re.M)
    deep_s = list(dict.fromkeys(re.findall(r"S\d+", text.split("## 深掘りの素材")[-1].split("## 1日1スキル")[0])))
    a, b = hosts[0], hosts[-1]

    def L(sp, t, src=()):
        return {"speaker": sp, "text": t, "style": "落ち着いて", "sources": list(src)}

    segs = [{"kind": "opening", "title": "オープニング", "lines": [L(a, "おはようございます。今日もニュースをお届けします。"), L(b, "よろしくお願いします。")]}]
    for i, sid in heads:
        segs.append({"kind": "headline", "title": f"テストのヘッドライン{i}", "lines": [
            L(a, f"{i}本目です。発表によると、売上は前年比12パーセント増えました。", [sid]),
            L(b, "それは大きいですね。何が理由なんでしょう。"),
            L(a, "発表は11月5日でした。背景はこれから詳しく見ていきます。", [sid])]})
    segs.append({"kind": "deep", "title": "テスト深掘り", "lines": [
        L(a, "今日の深掘りは取引の適正化です。制度は2026年4月に始まりました。", deep_s[:1]),
        L(b, "対象はどれくらいいるんですか。"),
        L(a, "約3万社とされています。", deep_s[1:2] or deep_s[:1]),
        L(a, "根拠のない数字の行です。市場は100兆円規模です。")]})
    segs.append({"kind": "skill", "title": "テストスキル", "lines": [L(a, "今日のスキルはSTPです。"), L(b, "セグメンテーション、ターゲティング、ポジショニングですね。")]})
    segs.append({"kind": "takeaway", "title": "示唆", "lines": [L(a, "番組としての提案は、価格の根拠を数字で示すことです。")]})
    segs.append({"kind": "closing", "title": "エンディング", "lines": [L(b, "番組ページの評価ボタンもぜひ押してください。")]})
    return {"title": "テスト回：取引の適正化を理解する", "summary": "テスト用の概要です。", "segments": segs}


def _factcheck(text: str) -> dict:
    ids = re.findall(r"^(L\d+)｜", text, flags=re.M)
    issues = []
    if len(ids) > 3:
        issues.append({"line_id": ids[3], "verdict": "fix", "fixed_text": "（テスト修正）発表は11月5日でした。", "reason": "テスト"})
    for lid in re.findall(r"^(L\d+)｜.*?100兆円", text, flags=re.M):
        issues.append({"line_id": lid, "verdict": "remove", "reason": "証拠にない数字"})
    return {"issues": issues, "summary": "テスト照合です。"}


def _research() -> dict:
    lines = ["- 制度は2026年4月に始まった。", "- 対象は約3万社とされる。", "- 個人ブログによると市場は100兆円規模。"]
    text = "\n".join(lines) + "\nまとめ: テスト用のまとめです。"
    chunks = [{"web": {"uri": f"https://vertexaisearch.cloud.google.com/grounding-api-redirect/{k}", "title": d, "domain": d}}
              for k, d in (("maff-1", "maff.go.jp"), ("meti-2", "meti.go.jp"), ("blog-3", "example-blog.com"))]
    supports = [{"segment": {"startIndex": 0, "endIndex": 10, "text": l[2:]}, "groundingChunkIndices": [i], "confidenceScores": [0.9]}
                for i, l in enumerate(lines)]
    return _text_resp(text, "gemini-2.5-flash", {"webSearchQueries": ["テスト 検索"], "groundingChunks": chunks,
                                                   "groundingSupports": supports})


def handle_generate(path: str, body: dict) -> tuple[int, dict]:
    gen = _k(body, "generationConfig", "generation_config") or {}
    mods = [str(x).upper() for x in (_k(gen, "responseModalities", "response_modalities") or [])]
    if "AUDIO" in mods:
        return _tts(body, gen)
    tools = json.dumps(_k(body, "tools") or [])
    text = _prompt(body)
    if "googleSearch" in tools or "google_search" in tools:
        return 200, _research()
    mime = _k(gen, "responseMimeType", "response_mime_type") or ""
    if "json" in mime:
        step = (re.search(r"\[step:(\w+)\]", text) or [None, ""])[1]
        obj = {"select": _plan, "script": _script, "factcheck": _factcheck}.get(step, lambda t: {})(text)
        return 200, _text_resp(json.dumps(obj, ensure_ascii=False))
    return 200, _text_resp("OK")


def _tts(body: dict, gen: dict) -> tuple[int, dict]:
    with _lock:
        COUNTER["tts"] += 1
        n = COUNTER["tts"]
    if os.environ.get("MOCK_TTS_ALWAYS_FAIL") == "1":  # 1日の上限切れ → VOICEVOX への切り替えを確認
        return 429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                               "message": "Quota exceeded for metric generate_requests_per_model_per_day, quotaId GenerateRequestsPerDayPerProjectPerModel (mock)"}}
    if n == 1:  # 1回目はわざと混雑エラー（再試行の確認）
        return 429, {"error": {"code": 429, "message": "Resource has been exhausted (mock)", "status": "RESOURCE_EXHAUSTED"}}
    sc = _k(gen, "speechConfig", "speech_config") or {}
    ms = _k(sc, "multiSpeakerVoiceConfig", "multi_speaker_voice_config") or {}
    speakers = {_k(x, "speaker") for x in (_k(ms, "speakerVoiceConfigs", "speaker_voice_configs") or [])}
    parts = (_k(body, "contents") or [{}])[0].get("parts", [])
    if len(speakers) != 2:
        return 400, {"error": {"code": 400, "message": "need 2 speakers (mock)", "status": "INVALID_ARGUMENT"}}
    chars = 0
    for p in parts:
        meta = _k(p, "speechMetadata", "speech_metadata") or {}
        if _k(meta, "speaker") not in speakers or not p.get("text"):
            return 400, {"error": {"code": 400, "message": "every part needs speech_metadata.speaker (mock)", "status": "INVALID_ARGUMENT"}}
        chars += len(p["text"])
    data = base64.b64encode(_wav(min(20.0, 0.5 + chars * 0.01))).decode()
    return 200, {"candidates": [{"content": {"role": "model", "parts": [{"inlineData": {"mimeType": "audio/wav", "data": data}}]},
                                 "finishReason": "STOP", "index": 0}]}


# ---------- VOICEVOX ----------
SPEAKERS = [{"name": "春日部つむぎ", "speaker_uuid": "mock-1", "styles": [{"name": "ノーマル", "id": 8}]},
            {"name": "玄野武宏", "speaker_uuid": "mock-2", "styles": [{"name": "ノーマル", "id": 11}, {"name": "喜び", "id": 39}]}]


# ---------- 評価シート ----------
def handle_feedback(query: dict) -> dict:
    if (query.get("token") or [""])[0] != os.environ.get("FEEDBACK_TOKEN", ""):
        return {"ok": False, "error": "bad token"}
    ep = "20261105"
    return {"ok": True, "rows": [
        {"ts": "2026-11-05T21:00:00Z", "ep": ep, "segment": "1:headline", "interest": "AI・テック", "action": "like", "text": ""},
        {"ts": "2026-11-05T21:00:05Z", "ep": ep, "segment": "5:deep", "interest": "AI・テック", "action": "deeper", "text": ""},
        {"ts": "2026-11-05T21:00:09Z", "ep": ep, "segment": "2:headline", "interest": "世界情勢", "action": "dislike", "text": ""},
        {"ts": "2026-11-05T21:00:12Z", "ep": ep, "segment": "overall", "interest": "", "action": "rate4", "text": ""},
        {"ts": "2026-11-05T21:01:00Z", "ep": ep, "segment": "request", "interest": "", "action": "request",
         "text": "米の輸出で使える補助金について知りたい"},
    ]}


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, headers: dict | None = None):
        self.send_response(code)
        for k, v in (headers or {"Content-Type": "application/json"}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):  # noqa: N802
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/proxy":
            code, headers, body = handle_proxy(q.get("url", [""])[0])
            self._send(code, body, headers)
        elif u.path == "/feedback":
            self._json(200, handle_feedback(q))
        elif u.path == "/version":
            self._json(200, "0.24.1")
        elif u.path == "/speakers":
            self._json(200, SPEAKERS)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/audio_query":
            text = q.get("text", [""])[0]
            self._json(200, {"accent_phrases": [], "speedScale": 1.0, "pitchScale": 0.0, "intonationScale": 1.0,
                             "volumeScale": 1.0, "prePhonemeLength": 0.1, "postPhonemeLength": 0.1,
                             "outputSamplingRate": 24000, "outputStereo": False, "kana": text})
        elif u.path == "/synthesis":
            query = json.loads(raw or b"{}")
            self._send(200, _wav(min(3.0, 0.2 + len(query.get("kana", "")) * 0.02)), {"Content-Type": "audio/wav"})
        elif ":generateContent" in u.path:
            code, resp = handle_generate(u.path, json.loads(raw or b"{}"))
            self._json(code, resp)
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass


def start() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    os.environ.update({
        "GEMINI_BASE_URL": url + "/", "GEMINI_API_KEY": "mock-key",
        "PODCAST_MOCK_HTTP": url, "VOICEVOX_URL": url,
        "PODCAST_TTS_INTERVAL_SEC": "0", "PODCAST_RETRY_BASE_SEC": "0.05",
    })
    os.environ.setdefault("SITE_URL", "http://localhost:8000")
    return server

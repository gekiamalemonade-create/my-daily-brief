"""Gemini API の呼び出し（無料枠）

- json:     決まった形の JSON で答えさせる（選定・台本・照合）
- grounded: Google 検索つきで調べさせ、「どの文がどのページに基づくか」を受け取る（深掘り・スキル）
- モデルは設定の左から順に使い、1日の上限・未提供などで失敗したら次のモデルに切り替える
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .util import extract_json

log = logging.getLogger("podcast")

RETRYABLE = {429, 500, 502, 503, 504}

SYSTEM_RESEARCH = (
    "あなたは正確さを最優先するリサーチャーです。検索で見つけた信頼できる情報源に書かれていることだけを根拠にし、"
    "推測で補いません。分からないことは分からないと書きます。日本語で答えます。"
)
SYSTEM_EDITOR = "あなたは信頼性と聞きやすさを両立させるポッドキャストの編集長です。日本語で考え、日本語で書きます。"
SYSTEM_WRITER = "あなたはプロの構成作家です。与えられた素材の事実だけを使い、耳で聞いて分かりやすい日本語の台本を書きます。"
SYSTEM_CHECKER = "あなたは厳格なファクトチェッカーです。証拠にないことは事実として認めません。"


def make_client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY が設定されていません（GitHub の Secrets を確認）")
    base = os.environ.get("GEMINI_BASE_URL", "").strip()  # テスト用（通常は空）
    return genai.Client(api_key=key, http_options=types.HttpOptions(base_url=base) if base else None)


def _per_day_exhausted(e: Exception) -> bool:
    s = str(e)
    return "PerDay" in s or "per day" in s.lower() or "per_day" in s.lower()


@dataclass
class GroundedRun:
    text: str = ""
    chunks: list = field(default_factory=list)    # [{uri, title, domain}]
    supports: list = field(default_factory=list)  # [{text, chunks:[index]}]
    queries: list = field(default_factory=list)

    @classmethod
    def from_response(cls, resp) -> "GroundedRun":
        cand = (resp.candidates or [None])[0]
        gm = getattr(cand, "grounding_metadata", None)
        run = cls(text=resp.text or "")
        if gm is None:
            return run
        for c in gm.grounding_chunks or []:
            web = getattr(c, "web", None)
            run.chunks.append({"uri": getattr(web, "uri", "") or "", "title": getattr(web, "title", "") or "",
                               "domain": getattr(web, "domain", "") or ""} if web else {"uri": "", "title": "", "domain": ""})
        for s in gm.grounding_supports or []:
            seg = getattr(s, "segment", None)
            text = getattr(seg, "text", "") if seg else ""
            if text:
                run.supports.append({"text": text, "chunks": list(s.grounding_chunk_indices or [])})
        run.queries = list(gm.web_search_queries or [])
        return run


class Gemini:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = make_client()
        self.calls: list[dict] = []

    def _generate(self, step: str, models: list[str], contents, config):
        base_wait = float(os.environ.get("PODCAST_RETRY_BASE_SEC", "20"))
        last: Exception | None = None
        for model in models:
            for attempt in range(3):
                t0 = time.time()
                try:
                    resp = self.client.models.generate_content(model=model, contents=contents, config=config)
                    self._record(step, model, resp, t0)
                    return resp
                except genai_errors.APIError as e:
                    last = e
                    code = getattr(e, "code", None)
                    if code == 429 and _per_day_exhausted(e):
                        log.warning("  %s: %s は今日の無料枠を使い切りました", step, model)
                        break
                    if code == 404:
                        log.warning("  %s: %s はこのプロジェクトでは使えません（提供終了など）", step, model)
                        break
                    if code not in RETRYABLE:
                        log.warning("  %s: %s でエラー（%s）", step, model, str(e)[:160])
                        break
                    wait = base_wait * (attempt + 1)
                    log.warning("  %s: %s が混雑・上限（%s）。%.0f秒待って再試行", step, model, code, wait)
                    time.sleep(wait)
                except Exception as e:  # 通信の切断・タイムアウトなど（API の応答そのものが届かなかった）
                    last = e
                    wait = base_wait * (attempt + 1)
                    log.warning("  %s: %s との通信が切れました（%s）。%.0f秒待って再試行",
                                step, model, type(e).__name__, wait)
                    time.sleep(wait)
            if model != models[-1]:
                log.warning("  %s: 次のモデルに切り替えます", step)
        raise RuntimeError(f"{step}: Gemini を呼び出せませんでした（{last}）")

    def _record(self, step: str, model: str, resp, t0: float) -> None:
        u = getattr(resp, "usage_metadata", None)
        rec = {"step": step, "model": model, "seconds": round(time.time() - t0, 1),
               "input_tokens": getattr(u, "prompt_token_count", 0) or 0,
               "output_tokens": (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)}
        self.calls.append(rec)
        log.info("  Gemini %-16s %-24s in=%6d out=%5d %.0f秒", step, model, rec["input_tokens"], rec["output_tokens"], rec["seconds"])

    def json(self, step: str, role: str, prompt: str, schema: dict, system: str, max_output_tokens: int = 16384) -> dict:
        config = types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json", response_json_schema=schema,
            temperature=0.4, max_output_tokens=max_output_tokens)
        models = list(self.cfg.models[role])
        while models:
            resp = self._generate(step, models, prompt, config)
            text = resp.text or ""
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = extract_json(text)
            if isinstance(data, dict):
                return data
            # 空の応答や途中で切れた応答だったときは、次のモデルで1回だけやり直す
            log.warning("  %s: 出力を読み取れなかったので、次のモデルでやり直します", step)
            used = getattr(resp, "model_version", None) or models[0]
            models = [m for m in models if not str(used).startswith(m)][:1] if len(models) > 1 else []
        raise RuntimeError(f"{step}: 決まった形の出力を受け取れませんでした")

    def grounded(self, step: str, role: str, prompt: str, system: str = SYSTEM_RESEARCH) -> GroundedRun:
        config = types.GenerateContentConfig(
            system_instruction=system, tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.3, max_output_tokens=16384)
        resp = self._generate(step, self.cfg.models[role], prompt, config)
        run = GroundedRun.from_response(resp)
        log.info("  %s: 検索 %d 回 / 参照ページ %d / 根拠つきの文 %d", step, len(run.queries), len(run.chunks), len(run.supports))
        return run

"""3. 素材集め（ヘッドライン記事の本文・深掘り調査・スキル調査）と出典の台帳

深掘りとスキルは Google 検索つきで調べるが、「信頼リスト（sources.yaml の trusted と RSS の配信元）」
にあるページに基づく文だけを素材に残す。リスト外のページだけが根拠の文は使わない。
"""
from __future__ import annotations

import datetime as dt
import logging

from . import fetcher
from .gemini_llm import SYSTEM_RESEARCH
from .util import domain_allowed, host_of, normalize_url, render, truncate

log = logging.getLogger("podcast")

EXCERPT_CHARS = 3000

PUBLISHERS = {
    "nhk.or.jp": "NHK", "web.nhk": "NHK", "nikkei.com": "日本経済新聞", "jiji.com": "時事通信", "asahi.com": "朝日新聞",
    "yomiuri.co.jp": "読売新聞", "mainichi.jp": "毎日新聞", "sankei.com": "産経新聞", "jomo-news.co.jp": "上毛新聞",
    "agrinews.co.jp": "日本農業新聞", "reuters.com": "ロイター", "apnews.com": "AP通信", "bbc.com": "BBC", "bbc.co.uk": "BBC",
    "afpbb.com": "AFP", "bloomberg.co.jp": "ブルームバーグ", "bloomberg.com": "ブルームバーグ",
    "ft.com": "フィナンシャル・タイムズ", "economist.com": "エコノミスト", "kantei.go.jp": "首相官邸",
    "cao.go.jp": "内閣府", "meti.go.jp": "経済産業省", "chusho.meti.go.jp": "中小企業庁", "maff.go.jp": "農林水産省",
    "mof.go.jp": "財務省", "mhlw.go.jp": "厚生労働省", "soumu.go.jp": "総務省", "mlit.go.jp": "国土交通省",
    "stat.go.jp": "総務省統計局", "e-stat.go.jp": "政府統計の総合窓口", "mofa.go.jp": "外務省", "caa.go.jp": "消費者庁",
    "jftc.go.jp": "公正取引委員会", "fsa.go.jp": "金融庁", "digital.go.jp": "デジタル庁", "jetro.go.jp": "ジェトロ",
    "jfc.go.jp": "日本政策金融公庫", "smrj.go.jp": "中小企業基盤整備機構", "naro.go.jp": "農研機構", "boj.or.jp": "日本銀行",
    "jpx.co.jp": "日本取引所グループ", "pref.gunma.jp": "群馬県", "city.tomioka.lg.jp": "富岡市", "imf.org": "IMF",
    "worldbank.org": "世界銀行", "oecd.org": "OECD", "un.org": "国連", "wto.org": "WTO", "fao.org": "国連食糧農業機関",
    "iea.org": "国際エネルギー機関", "who.int": "WHO", "itmedia.co.jp": "ITmedia", "impress.co.jp": "インプレス",
    "markezine.jp": "MarkeZine", "advertimes.com": "AdverTimes", "hbr.org": "ハーバード・ビジネス・レビュー",
    "anthropic.com": "Anthropic", "openai.com": "OpenAI", "blog.google": "Google", "deepmind.google": "Google DeepMind",
    "microsoft.com": "Microsoft", "nvidia.com": "NVIDIA", "apple.com": "Apple", "about.fb.com": "Meta",
    "about.instagram.com": "Instagram", "newsroom.tiktok.com": "TikTok", "blog.youtube": "YouTube",
    "nature.com": "Nature", "science.org": "Science", "arxiv.org": "arXiv（査読前の論文）", "jstage.jst.go.jp": "J-STAGE",
    "thinkwithgoogle.com": "Think with Google",
}


def publisher_of(url: str) -> str:
    host = host_of(url)
    best = ""
    for dom in PUBLISHERS:
        if (host == dom or host.endswith("." + dom)) and len(dom) > len(best):
            best = dom
    return PUBLISHERS.get(best, host[4:] if host.startswith("www.") else host)


def _bare_domain(text: str) -> bool:
    """「maff.go.jp」のように、見出しではなくドメイン名だけの文字列か"""
    t = (text or "").strip().lower()
    return bool(t) and " " not in t and "." in t and all(ch.isascii() for ch in t)


class SourceRegistry:
    """出典の台帳。台本の各行は S1, S2 ... のIDで出典を指す"""

    def __init__(self):
        self.items: list[dict] = []
        self._by_url: dict[str, int] = {}

    def add(self, url: str, title: str = "", publisher: str = "", date: str = "", segment: str = "",
            excerpt: str = "", cited: str = "") -> str:
        n = normalize_url(url)
        if n in self._by_url:
            s = self.items[self._by_url[n]]
            if cited and cited not in s["cited"]:
                s["cited"].append(cited)
            for k, v in (("excerpt", excerpt), ("title", title), ("date", date)):
                if v and not s[k]:
                    s[k] = v
            # 検索の出典名はドメインだけ（例: maff.go.jp）のことが多いので、記事ページの見出しが取れたら置き換える
            if title and _bare_domain(s["title"]) and not _bare_domain(title):
                s["title"] = title
            if segment and segment not in s["segments"]:
                s["segments"].append(segment)
            return s["id"]
        sid = f"S{len(self.items) + 1}"
        self.items.append({"id": sid, "url": url, "title": title or "", "publisher": publisher or publisher_of(url),
                           "date": date or "", "segments": [segment] if segment else [], "excerpt": excerpt or "",
                           "cited": [cited] if cited else []})
        self._by_url[n] = len(self.items) - 1
        return sid


def gather(cfg, llm, plan: dict, candidates: list[dict], now: dt.datetime) -> dict:
    reg = SourceRegistry()
    by_id = {c["id"]: c for c in candidates}
    trusted = cfg.trusted_domains()
    chars = int(cfg.collect.get("article_chars", 2500))
    today = now.date().isoformat()

    # (1) ヘッドライン: 記事ページを開いて本文を取り出す（取れなければ RSS の要約だけ使う）
    head_mats = []
    for h in plan["headlines"]:
        c = by_id[h["candidate_id"]]
        art = fetcher.fetch_article(c["url"], EXCERPT_CHARS)
        if not art["ok"]:
            log.info("  本文なし: %s（%s）", c["title"][:40], art["reason"])
        pub = publisher_of(c["url"])
        sid = reg.add(c["url"], c["title"], pub, c["published"][:10], "headline",
                      truncate(art["text"], EXCERPT_CHARS) if art["ok"] else "", c["summary"])
        head_mats.append({"sid": sid, "candidate_id": c["id"], "title": c["title"], "interest": h["interest"],
                          "publisher": pub, "published": c["published"], "summary": c["summary"],
                          "excerpt": truncate(art["text"], chars) if art["ok"] else "", "fetched": art["ok"]})
    if head_mats:
        log.info("素材: ヘッドライン %d 本（本文を取得できた記事 %d 本）", len(head_mats), sum(h["fetched"] for h in head_mats))

    # (2) 深掘り・(3) 1日1スキル（Google 検索つき）
    deep = plan["deep_dive"]
    seeds = [by_id[x] for x in deep.get("seed_candidate_ids", []) if x in by_id]
    deep_mat = _research(cfg, llm, reg, "deep", "今日の深掘り", deep["title"], deep.get("angle", ""),
                         deep.get("questions", []), seeds, int(cfg.collect.get("deep_fetches", 3)), trusted, today)
    deep_mat["interest"] = deep.get("interest", "その他")
    sk = plan["skill"]
    skill_mat = _research(cfg, llm, reg, "skill", "1日1スキル", sk["title"],
                          f"学習トラック「{sk['track']}」の1回分。定義・具体例・今日から使えるコツ",
                          sk.get("questions", []), [], int(cfg.collect.get("skill_fetches", 2)), trusted, today)
    skill_mat["track"] = sk["track"]
    return {"headlines": head_mats, "deep": deep_mat, "skill": skill_mat, "sources": reg.items}


def _research(cfg, llm, reg: SourceRegistry, segment: str, corner: str, title: str, angle: str, questions: list[str],
              seeds: list[dict], n_fetch: int, trusted: list[str], today: str) -> dict:
    log.info("素材: %s「%s」を調査", corner, title)
    lines: list[str] = []
    for c in seeds:  # きっかけの記事は RSS の配信元なので、そのまま素材に入れる
        art = fetcher.fetch_article(c["url"], EXCERPT_CHARS)
        sid = reg.add(c["url"], c["title"], publisher_of(c["url"]), c["published"][:10], segment,
                      art["text"] if art["ok"] else "", c["summary"])
        lines.append(f"- きっかけの記事「{c['title']}」（{c['source']}）: {c['summary']} [{sid}]")

    prompt = render(
        cfg.prompt("research"), today=today, corner=corner, title=title, angle=angle or "（指定なし）",
        questions="\n".join(f"- {q}" for q in questions) or "- テーマを理解するうえで重要な点",
        seeds="\n".join(f"- {c['title']}（{c['source']}）" for c in seeds) or "（なし）",
        listener=cfg.profile.get("name", "リスナー"),
    )
    try:
        run = llm.grounded(f"research_{segment}", "research", prompt, SYSTEM_RESEARCH)
    except RuntimeError as e:
        log.warning("素材: %s の調査ができませんでした（%s）。きっかけ記事だけで進めます", corner, e)
        return {"title": title, "angle": angle, "notes": "\n".join(lines),
                "sources": [x["id"] for x in reg.items if segment in x["segments"]], "dropped": 0}

    # 出典ごとに、信頼リストにあるか（実際のURLで）確かめる
    real: dict[int, str] = {}
    for i, ch in enumerate(run.chunks[:15]):
        hint = (ch.get("domain") or ch.get("title") or "").strip().lower()
        if hint and " " not in hint and "." in hint and not domain_allowed(f"https://{hint}/", trusted):
            continue  # 見た目のドメインでリスト外と分かるものは、開かずに捨てる
        url = fetcher.resolve_redirect(ch["uri"]) if ch.get("uri") else ""
        if url and domain_allowed(url, trusted):
            real[i] = url

    dropped, fetch_targets = 0, []
    for s in run.supports:
        idx = [i for i in s["chunks"] if i in real]
        if not idx:
            dropped += 1
            continue
        sids = []
        for i in idx:
            sid = reg.add(real[i], run.chunks[i].get("title", ""), "", "", segment, "", s["text"].strip())
            if sid not in sids:
                sids.append(sid)
            if real[i] not in fetch_targets:
                fetch_targets.append(real[i])
        lines.append(f"- {s['text'].strip()} [{','.join(sids)}]")

    for url in fetch_targets[:n_fetch]:  # 照合用に本文も確かめる
        art = fetcher.fetch_article(url, EXCERPT_CHARS)
        if art["ok"]:
            reg.add(url, art["title"], "", "", segment, art["text"])
    log.info("素材: %s 根拠つきの文 %d（信頼リスト外のため除外 %d）", corner, len(lines) - len(seeds), dropped)
    if len(lines) == len(seeds):
        log.warning("素材: %s で信頼できる出典が見つかりませんでした（台本では一般的な説明にとどめます）", corner)
    return {"title": title, "angle": angle, "notes": "\n".join(lines),
            "sources": [x["id"] for x in reg.items if segment in x["segments"]], "dropped": dropped}

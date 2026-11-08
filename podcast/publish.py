"""7. 配信（MP3・番組ページ・ポッドキャスト用 RSS を site/ に書き出す。site/ は gh-pages ブランチとして公開される）"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import re
import shutil
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xesc

from .util import read_json, write_json
from .writer import kind_label

log = logging.getLogger("podcast")

E = html.escape

CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#272c37;--text:#e8eaf0;--muted:#9aa3b2;--accent:#7cc4ff;--ok:#5bd69b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);line-height:1.75;
font-family:system-ui,-apple-system,"Hiragino Sans","Noto Sans JP","Yu Gothic",sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 72px}a{color:var(--accent)}
h1{font-size:1.45rem;line-height:1.4;margin:.3em 0}h2{font-size:1rem;margin:2em 0 .6em;color:var(--muted);letter-spacing:.05em}
.meta{color:var(--muted);font-size:.9rem}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
audio{width:100%;margin:14px 0}.kind{display:inline-block;font-size:.72rem;color:#0f1115;background:var(--accent);
border-radius:999px;padding:1px 10px;margin-right:8px;vertical-align:2px}.ttl{font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.chip{border:1px solid var(--line);background:#1e232c;color:var(--text);
border-radius:999px;padding:9px 15px;font-size:.95rem;cursor:pointer}.chip.sent{border-color:var(--ok);color:var(--ok)}
ul.src{font-size:.86rem;color:var(--muted);margin:.6em 0 0;padding-left:1.2em}textarea{width:100%;min-height:72px;background:#0b0d11;
color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px;font:inherit}details summary{cursor:pointer;color:var(--muted)}
.line{margin:.55em 0}.who{color:var(--accent);font-weight:600;margin-right:.4em}.fixed{border-left:3px solid var(--ok);padding-left:8px}
code{background:#0b0d11;padding:2px 6px;border-radius:6px;word-break:break-all}.ep a{text-decoration:none}.note{font-size:.85rem;color:var(--muted)}
"""

JS = """
function sendFb(btn, params){
  if(!FB.url){btn.textContent='評価ボタンは未設定です（README の STEP 9）';return;}
  const q=new URLSearchParams(Object.assign({token:FB.token,ep:FB.ep},params));
  fetch(FB.url+(FB.url.includes('?')?'&':'?')+q.toString(),{mode:'no-cors'})
    .then(()=>{btn.classList.add('sent');if(!btn.dataset.done){btn.textContent+=' ✓';btn.dataset.done='1';}})
    .catch(()=>{btn.textContent='送信できませんでした';});
}
document.querySelectorAll('[data-fb]').forEach(b=>b.addEventListener('click',()=>sendFb(b,JSON.parse(b.dataset.fb))));
const rb=document.getElementById('reqbtn');
if(rb){rb.addEventListener('click',()=>{const t=document.getElementById('reqtext');const v=t.value.trim();if(!v)return;
  sendFb(rb,{segment:'request',interest:'',action:'request',text:v});t.value='';});}
"""


def site_url() -> str:
    return os.environ.get("SITE_URL", "").strip().rstrip("/") or "http://localhost:8000"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "podcast"


def _page(title: str, body: str, script: str = "") -> str:
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
            f'<title>{E(title)}</title><style>{CSS}</style></head><body><main>{body}</main>'
            + (f"<script>{script}</script>" if script else "") + "</body></html>")


def _fb_button(label: str, params: dict) -> str:
    return f'<button class="chip" data-fb="{E(json.dumps(params, ensure_ascii=False), quote=True)}">{E(label)}</button>'


def _fmt_duration(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}分{sec % 60:02d}秒" if sec else "音声なし"


def episode_page(cfg, ep: dict, script: dict, report: dict) -> str:
    names = {h["id"]: h["name"] for h in cfg.hosts}
    fb_conf = {"url": os.environ.get("FEEDBACK_URL", ""), "token": os.environ.get("FEEDBACK_TOKEN", ""), "ep": ep["id"]}
    parts = [f'<p class="meta"><a href="../index.html">{E(cfg.show["title"])}</a> ／ {E(ep["date"])} ／ {_fmt_duration(ep["duration"])}</p>',
             f"<h1>{E(ep['title'])}</h1>"]
    if ep.get("mp3"):
        parts.append(f'<audio controls preload="none" src="{E(ep["mp3"])}"></audio>')
    parts.append(f"<p>{E(ep['summary'])}</p>")

    parts.append("<h2>今日の内容と出典（タップで評価すると、次回からの選び方に反映されます）</h2>")
    for i, s in enumerate(ep["segments"]):
        if s["kind"] in ("opening", "closing"):
            continue
        src = "".join(f'<li>{E(x["publisher"])}「<a href="{E(x["url"])}" rel="noopener" target="_blank">{E(x["title"] or x["url"])}</a>」'
                      f'{(" " + E(x["date"])) if x.get("date") else ""}</li>' for x in s["sources"])
        base = {"segment": f"{i}:{s['kind']}", "interest": s.get("interest", "")}
        chips = (_fb_button("👍 よかった", {**base, "action": "like"}) + _fb_button("🔍 もっと深く", {**base, "action": "deeper"})
                 + _fb_button("👎 いまいち", {**base, "action": "dislike"}))
        parts.append(f'<div class="card"><span class="kind">{E(kind_label(s["kind"], cfg))}</span>'
                     f'<span class="ttl">{E(s["title"])}</span>'
                     + (f'<ul class="src">{src}</ul>' if src else '<p class="note">出典: なし（一般的な説明のコーナー）</p>')
                     + f'<div class="chips">{chips}</div></div>')

    rate = "".join(_fb_button(f"{n}", {"segment": "overall", "interest": "", "action": f"rate{n}"}) for n in range(1, 6))
    parts.append(f'<h2>今日の満足度</h2><div class="chips">{rate}</div>')
    parts.append('<h2>取り上げてほしいテーマ（次回以降の深掘りの候補になります）</h2>'
                 '<textarea id="reqtext" maxlength="200" placeholder="例: 米の輸出で使える補助金"></textarea>'
                 '<div class="chips"><button class="chip" id="reqbtn">送る</button></div>')

    fc = ep["factcheck"]
    issues = "".join(
        f"<li>{'修正' if it['verdict'] == 'fix' else '削除'}: {E(it['before'])}" + (f" → {E(it['after'])}" if it.get("after") else "")
        + f"<br><span class='note'>理由: {E(it.get('reason', ''))}</span></li>" for it in report.get("issues", []))
    parts.append(f'<h2>ファクトチェック</h2><div class="card">台本 {fc["checked_lines"]} 行を出典と照合し、'
                 f'修正 {fc["fixed"]} 件・削除 {fc["removed"]} 件。{E(fc.get("summary", ""))}'
                 + (f"<details><summary>詳細</summary><ul>{issues}</ul></details>" if issues else "")
                 + '<p class="note">AIによる照合です。重要な判断の前には、上の出典を開いて確認してください。</p></div>')

    lines = "".join(
        f'<div class="line{" fixed" if l.get("checked") == "fixed" else ""}"><span class="who">{E(names.get(l["speaker"], l["speaker"]))}</span>{E(l["text"])}</div>'
        for s in script["segments"] for l in s["lines"])
    parts.append(f"<h2>台本</h2><details><summary>全文を表示</summary>{lines}</details>")
    if ep.get("credits"):
        parts.append(f'<p class="note">音声合成: {E(ep["credits"])}</p>')
    parts.append(f'<p class="note">{E(ep.get("usage_note", ""))}</p>')
    fb_json = json.dumps(fb_conf, ensure_ascii=False).replace("</", "<\\/")
    return _page(f"{ep['title']}｜{cfg.show['title']}", "".join(parts), f"const FB={fb_json};" + JS)


def index_page(cfg, episodes: list[dict]) -> str:
    feed = f"{site_url()}/feed.xml"
    rows = "".join(
        f'<div class="card ep"><a href="episodes/{E(e["id"])}.html"><span class="meta">{E(e["date"])} ／ {_fmt_duration(e["duration"])}</span>'
        f'<div class="ttl">{E(e["title"])}</div></a><div class="note">{E(e["summary"])}</div></div>' for e in episodes)
    body = (f"<h1>{E(cfg.show['title'])}</h1><p class='meta'>{E(cfg.show.get('subtitle', ''))}</p>"
            f"<div class='card'>ポッドキャストアプリに登録するURL:<br><code>{E(feed)}</code>"
            "<p class='note'>iPhone: Podcast アプリ → ライブラリ → 右上の「…」→「URLで番組をフォロー」に貼り付け。"
            "このURLは他の人に共有しないでください。</p></div>"
            f"<h2>エピソード</h2>{rows or '<p>まだありません</p>'}")
    return _page(cfg.show["title"], body)


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def feed_xml(cfg, episodes: list[dict], now: dt.datetime) -> str:
    base = site_url()
    title = cfg.show["title"]
    items = []
    for e in episodes:
        if not e.get("mp3"):
            continue
        pub = dt.datetime.fromisoformat(e["pub"])
        desc = [f"<p>{E(e['summary'])}</p><ul>"]
        for s in e["segments"]:
            if s["kind"] in ("opening", "closing"):
                continue
            desc.append(f"<li>{E(kind_label(s['kind'], cfg))}: {E(s['title'])}</li>")
        desc.append(f'</ul><p><a href="{base}/episodes/{e["id"]}.html">出典・評価ボタン・台本はこちら</a></p>')
        if e.get("credits"):
            desc.append(f"<p>音声合成: {E(e['credits'])}</p>")
        items.append(
            "<item>"
            f"<title>{xesc(pub.strftime('%m/%d') + ' ' + e['title'])}</title>"
            f"<link>{xesc(base)}/episodes/{e['id']}.html</link>"
            f'<guid isPermaLink="false">{xesc(e["guid"])}</guid>'
            f"<pubDate>{format_datetime(pub)}</pubDate>"
            f"<description>{_cdata(''.join(desc))}</description>"
            f'<enclosure url="{xesc(base)}/episodes/{xesc(e["mp3"])}" length="{int(e["bytes"])}" type="audio/mpeg"/>'
            f"<itunes:duration>{int(e['duration'])}</itunes:duration>"
            "<itunes:episodeType>full</itunes:episodeType><itunes:explicit>false</itunes:explicit>"
            "</item>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>'
        f"<title>{xesc(title)}</title><link>{xesc(base)}/</link>"
        f'<atom:link href="{xesc(base)}/feed.xml" rel="self" type="application/rss+xml"/>'
        f"<language>{xesc(cfg.show.get('language', 'ja'))}</language>"
        f"<description>{xesc(cfg.show.get('subtitle', title))}</description>"
        f"<itunes:author>{xesc(cfg.show.get('author', ''))}</itunes:author>"
        f'<itunes:image href="{xesc(base)}/cover.png"/>'
        '<itunes:category text="News"><itunes:category text="Daily News"/></itunes:category>'
        "<itunes:explicit>false</itunes:explicit><itunes:type>episodic</itunes:type>"
        "<itunes:block>Yes</itunes:block>"  # Apple Podcasts の検索に載せない（自分専用）
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n</channel></rss>\n")


def publish(cfg, site_dir, now: dt.datetime, script: dict, mats: dict, report: dict, mp3_src, audio_info: dict,
            usage_note: str = "", credits: str = "") -> dict:
    site = Path(site_dir)
    eps_dir = site / "episodes"
    eps_dir.mkdir(parents=True, exist_ok=True)
    ep_id, stamp = now.strftime("%Y%m%d"), now.strftime("%H%M%S")
    episodes = read_json(site / "episodes.json", [])
    for old in [e for e in episodes if e["id"] == ep_id]:  # 同じ日の作り直し
        if old.get("mp3"):
            (eps_dir / old["mp3"]).unlink(missing_ok=True)
    episodes = [e for e in episodes if e["id"] != ep_id]

    mp3_name = ""
    if mp3_src:
        mp3_name = f"{ep_id}-{stamp}.mp3"  # 作り直したときにアプリが新しい音声を取り直せるよう、名前に時刻を入れる
        shutil.copy(mp3_src, eps_dir / mp3_name)

    src_by_id = {s["id"]: s for s in mats["sources"]}
    segs = []
    for s in script["segments"]:
        sids = [s["main_source"]] if s.get("main_source") else []
        for l in s["lines"]:
            sids += [x for x in l["sources"] if x not in sids]
        segs.append({"kind": s["kind"], "title": s["title"], "interest": s.get("interest", ""),
                     "sources": [{k: src_by_id[x][k] for k in ("id", "title", "publisher", "url", "date")}
                                 for x in sids if x in src_by_id]})
    ep = {
        "id": ep_id, "date": now.date().isoformat(), "pub": now.isoformat(timespec="seconds"),
        "title": script["title"], "summary": script["summary"], "mp3": mp3_name,
        "bytes": int(audio_info.get("bytes", 0)), "duration": float(audio_info.get("duration", 0)),
        "guid": f"{_slug(cfg.show['title'])}-{ep_id}-{stamp}", "segments": segs,
        "factcheck": {k: report.get(k) for k in ("checked_lines", "fixed", "removed", "summary")},
        "usage_note": usage_note, "credits": credits,
    }
    episodes.insert(0, ep)
    episodes.sort(key=lambda e: e["id"], reverse=True)
    keep = int(cfg.show.get("keep_episodes", 21))
    for old in episodes[keep:]:
        if old.get("mp3"):
            (eps_dir / old["mp3"]).unlink(missing_ok=True)
        (eps_dir / f"{old['id']}.html").unlink(missing_ok=True)
    episodes = episodes[:keep]

    write_json(site / "episodes.json", episodes)
    (eps_dir / f"{ep_id}.html").write_text(episode_page(cfg, ep, script, report), encoding="utf-8")
    (site / "index.html").write_text(index_page(cfg, episodes), encoding="utf-8")
    (site / "feed.xml").write_text(feed_xml(cfg, episodes, now), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    cover = cfg.root / "assets" / "cover.png"
    if cover.exists():
        shutil.copy(cover, site / "cover.png")
    log.info("配信: %s/episodes/%s.html（保存中のエピソード %d 本）", site_url(), ep_id, len(episodes))
    return ep

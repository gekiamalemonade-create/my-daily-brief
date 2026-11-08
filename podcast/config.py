"""設定ファイル（config/*.yaml）の読み込みと検証（無料版）"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .util import host_of

PKG_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    root: Path
    show: dict
    profile: dict
    sources: dict
    private_context: str = ""

    # ---- よく使う値 ----
    @property
    def hosts(self) -> list[dict]:
        return self.show["hosts"]

    @property
    def host_ids(self) -> list[str]:
        return [h["id"] for h in self.hosts]

    @property
    def models(self) -> dict:
        return self.show["models"]

    @property
    def collect(self) -> dict:
        return self.show["collect"]

    @property
    def tts(self) -> dict:
        return self.show["tts"]

    @property
    def feeds(self) -> list[dict]:
        return self.sources.get("feeds", [])

    @property
    def interest_ids(self) -> list[str]:
        return [i["id"] for i in self.profile.get("interests", [])]

    def prompt(self, name: str) -> str:
        return (self.root / "prompts" / f"{name}.md").read_text(encoding="utf-8")

    def trusted_domains(self) -> list[str]:
        """出典として使ってよいドメイン（trusted のグループ＋RSS の配信元＋テスト用の追加分）"""
        out: list[str] = []
        for domains in self.sources.get("trusted", {}).values():
            for d in domains:
                if d not in out:
                    out.append(d)
        for f in self.feeds:
            h = host_of(f["url"])
            h = h[4:] if h.startswith("www.") else h
            if h and h not in out:
                out.append(h)
        for d in os.environ.get("PODCAST_EXTRA_TRUSTED", "").split(","):
            if d.strip() and d.strip() not in out:
                out.append(d.strip())
        return out

    def profile_text(self) -> str:
        """プロンプトに入れるリスナー紹介文"""
        p = self.profile
        lines = [f"名前: {p.get('name', '')}", f"紹介: {p.get('about', '')}"]
        if p.get("goals"):
            lines.append("目的: " + " / ".join(p["goals"]))
        lines.append("興味分野: " + " / ".join(f"{i['id']}（{i.get('desc', '')}）" for i in p.get("interests", [])))
        if self.private_context:
            lines.append("補足（本人が書いた詳しい状況）: " + self.private_context)
        return "\n".join(lines)

    def ventures_text(self) -> str:
        items = list(self.profile.get("ventures", []))
        text = "\n".join(f"- {v}" for v in items) or "- （未設定）"
        if self.private_context:
            text += f"\n- 詳しい状況: {self.private_context}"
        return text


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def validate(cfg: Config) -> None:
    hosts = cfg.show.get("hosts", [])
    _check(len(hosts) == 2, "show.yaml の hosts はちょうど2人にしてください（Gemini の掛け合い音声は2人まで）")
    for h in hosts:
        _check(bool(h.get("id")) and h["id"].isascii() and h["id"].isalnum(), f"host の id は半角英数字にしてください: {h.get('id')}")
        _check(bool(h.get("voice")), f"host {h['id']} の voice が空です")
    for role in ("editor", "research", "script", "factcheck"):
        models = cfg.models.get(role)
        _check(isinstance(models, list) and len(models) > 0, f"show.yaml の models.{role} はモデル名のリストにしてください")
    _check(cfg.tts.get("engine", "gemini") in ("gemini", "voicevox"), "tts.engine は gemini か voicevox にしてください")
    ids = cfg.interest_ids
    _check(len(ids) > 0, "profile.yaml の interests が空です")
    _check(len(ids) == len(set(ids)), "profile.yaml の interests の id が重複しています")
    for i in cfg.profile["interests"]:
        _check(1 <= float(i.get("weight", 0)) <= 5, f"weight は1〜5にしてください: {i['id']}")
    _check(len(cfg.feeds) > 0, "sources.yaml の feeds が空です")
    for f in cfg.feeds:
        _check(bool(f.get("name")) and str(f.get("url", "")).startswith(("http://", "https://")), f"feeds の書き方が正しくありません: {f}")
        for it in f.get("interests", []):
            _check(it in ids, f"RSS「{f['name']}」の興味分野「{it}」が profile.yaml にありません")
    for name, domains in cfg.sources.get("trusted", {}).items():
        for d in domains:
            _check(d.isascii() and "://" not in d and " " not in d and "*" not in d,
                   f"sources.yaml の trusted の書き方が正しくありません（{name}: {d}）")


def load(root: Path | None = None) -> Config:
    root = Path(root or PKG_ROOT)

    def y(name: str) -> dict:
        return yaml.safe_load((root / "config" / name).read_text(encoding="utf-8")) or {}

    profile_env = os.environ.get("PROFILE_YAML", "").strip()
    profile = yaml.safe_load(profile_env) if profile_env else y("profile.yaml")
    cfg = Config(root=root, show=y("show.yaml"), profile=profile, sources=y("sources.yaml"),
                 private_context=os.environ.get("PRIVATE_CONTEXT", "").strip())
    validate(cfg)
    return cfg

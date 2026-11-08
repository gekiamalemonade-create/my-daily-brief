"""過去に扱ったテーマと、学習した好みの保存（state/ フォルダ。GitHub Actions が毎回コミットする）"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .util import read_json, write_json

MAX_HISTORY = 90


class State:
    def __init__(self, root: Path):
        self.dir = Path(root) / "state"
        self.history: list[dict] = read_json(self.dir / "history.json", [])
        self.learned: dict = read_json(self.dir / "learned.json", {})
        self.learned.setdefault("weight_delta", {})
        self.learned.setdefault("skill_progress", {})
        self.learned.setdefault("processed_feedback", [])
        self.learned.setdefault("used_requests", [])

    def recent(self, days: int, today: dt.date) -> list[dict]:
        since = (today - dt.timedelta(days=days)).isoformat()
        return [h for h in self.history if h.get("date", "") >= since and h.get("date") != today.isoformat()]

    def add_episode(self, record: dict) -> None:
        self.history = [h for h in self.history if h.get("date") != record["date"]]
        self.history.insert(0, record)
        self.history.sort(key=lambda h: h["date"], reverse=True)
        del self.history[MAX_HISTORY:]

    def add_skill(self, track: str, title: str) -> None:
        done = self.learned["skill_progress"].setdefault(track, [])
        if title not in done:
            done.append(title)
        del done[:-30]

    def save(self) -> None:
        write_json(self.dir / "history.json", self.history)
        write_json(self.dir / "learned.json", self.learned)

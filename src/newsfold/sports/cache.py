"""File-based TTL cache for the sports snapshot — no DB table (see CLAUDE.md's
`sports/` layer rule: this is a low-volume, read-only cache of external API
responses, not pipeline/reader-state data). One JSON file per cache key under
`data/sports_cache/` (covered by the existing git-ignored `data/` directory)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from newsfold.config import DATA_DIR
from newsfold.sports.models import Event
from newsfold.timeutil import utcnow

CACHE_DIR = DATA_DIR / "sports_cache"


def _path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def write_cache(key: str, events: list[Event]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": utcnow().isoformat(),
        "events": [
            {**asdict(e), "start_time": e.start_time.isoformat() if e.start_time else None}
            for e in events
        ],
    }
    _path(key).write_text(json.dumps(payload), encoding="utf-8")


def read_cache(key: str) -> tuple[list[Event], datetime] | None:
    path = _path(key)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    fetched_at = datetime.fromisoformat(data["fetched_at"])
    events = [
        Event(
            sport=e["sport"],
            title=e["title"],
            subtitle=e["subtitle"],
            start_time=datetime.fromisoformat(e["start_time"]) if e["start_time"] else None,
            status=e["status"],
            result=e["result"],
            detail_url=e["detail_url"],
        )
        for e in data["events"]
    ]
    return events, fetched_at


def is_stale(fetched_at: datetime, ttl_minutes: int) -> bool:
    return (utcnow() - fetched_at).total_seconds() > ttl_minutes * 60

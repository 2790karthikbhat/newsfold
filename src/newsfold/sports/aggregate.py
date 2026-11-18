"""Fetch all three sports providers, cache the combined result, and expose a
read-only snapshot for the CLI and the Glance web routes.

Refresh is explicit only (`digest sports fetch`, e.g. via cron) — never inside
`digest run` (CLAUDE.md's `sports/` layer rule, same "deliberate, not automatic"
precedent as `retention`). `load_cached` never calls a provider; it only reads
whatever `refresh_all` last wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from newsfold.config import load_settings
from newsfold.sports import cache
from newsfold.sports.cricinfo import fetch_live_cricket
from newsfold.sports.cricket import fetch_cricket
from newsfold.sports.f1 import fetch_f1
from newsfold.sports.football import fetch_football
from newsfold.sports.models import Event

_CACHE_KEY = "sports"


@dataclass
class SportsRefreshReport:
    football_count: int = 0
    cricket_count: int = 0
    f1_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class SportsSnapshot:
    events: list[Event]
    fetched_at: datetime
    stale: bool


def refresh_all(*, dry_run: bool = False) -> SportsRefreshReport:
    report = SportsRefreshReport()
    if dry_run:
        return report

    cfg = load_settings().section("sports")
    events: list[Event] = []

    football_result = fetch_football(cfg)
    events.extend(football_result.events)
    report.football_count = len(football_result.events)
    if football_result.error:
        report.errors.append(f"football: {football_result.error}")

    cricket_result = fetch_cricket(cfg)
    events.extend(cricket_result.events)
    report.cricket_count = len(cricket_result.events)
    if cricket_result.error:
        report.errors.append(f"cricket: {cricket_result.error}")

    live_cricket_result = fetch_live_cricket(cfg)
    events.extend(live_cricket_result.events)
    report.cricket_count += len(live_cricket_result.events)
    if live_cricket_result.error:
        report.errors.append(f"cricket-live: {live_cricket_result.error}")

    f1_result = fetch_f1(cfg)
    events.extend(f1_result.events)
    report.f1_count = len(f1_result.events)
    if f1_result.error:
        report.errors.append(f"f1: {f1_result.error}")

    cache.write_cache(_CACHE_KEY, events)
    return report


def load_cached() -> SportsSnapshot | None:
    ttl = int(load_settings().section("sports").get("cache_ttl_minutes", 60))
    result = cache.read_cache(_CACHE_KEY)
    if result is None:
        return None
    events, fetched_at = result
    return SportsSnapshot(
        events=events, fetched_at=fetched_at, stale=cache.is_stale(fetched_at, ttl)
    )

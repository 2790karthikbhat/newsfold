"""Live-in-progress cricket matches via ESPN Cricinfo's public live-scores RSS
feed — a supplement to `cricket.py`'s bigballsdata-sourced schedule/results,
filling the "live match" slot bigballsdata rarely has data for.

ESPN Cricinfo has far broader international coverage than bigballsdata (a real India vs
Afghanistan T20I was entirely absent from bigballsdata's archive), but this feed
is narrowly a *live scores ticker*: no schedule, no past results, one item per
match with the team names and live score run together in a single free-text
title ("Team A 227/10  v Team B 174/3 *") rather than structured fields, and no
per-item timestamp at all. Team names are extracted with a best-effort split on
" v " for matching against followed teams; the raw title (score included) is
kept as-is for display rather than reformatted, since further parsing of the
score fragment would be guesswork.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import feedparser
import httpx

from newsfold.ingest.common import make_client
from newsfold.sports.matching import matches_team
from newsfold.sports.models import Event

_URL = "https://www.cricinfo.com/rss/livescores.xml"
_SPLIT_RE = re.compile(r"\s+v\s+")


@dataclass(frozen=True)
class CricinfoResult:
    ok: bool
    events: tuple[Event, ...] = ()
    error: str | None = None


def _fetch_titles(client: httpx.Client) -> list[str]:
    resp = client.get(_URL)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    return [str(entry.get("title", "")).strip() for entry in parsed.entries if entry.get("title")]


def fetch_live_cricket(
    cfg: dict[str, Any], *, client: httpx.Client | None = None
) -> CricinfoResult:
    cricket_cfg = cfg.get("cricket") or {}
    teams = tuple(cricket_cfg.get("teams") or ())
    if not teams:
        return CricinfoResult(ok=True, events=())

    owns_client = client is None
    active_client = client or make_client(float(cfg.get("http_timeout_sec", 20)))
    try:
        titles = _fetch_titles(active_client)
    except httpx.HTTPError as exc:
        return CricinfoResult(ok=False, error=str(exc))
    finally:
        if owns_client:
            active_client.close()

    events: list[Event] = []
    for title in titles:
        parts = _SPLIT_RE.split(title, maxsplit=1)
        if len(parts) != 2:
            continue
        left, right = parts
        if not any(matches_team(t, left, right) for t in teams):
            continue
        events.append(
            Event(
                sport="cricket",
                title=title,
                subtitle="Live now",
                start_time=None,
                status="live",
                result=None,
            )
        )
    return CricinfoResult(ok=True, events=tuple(events))

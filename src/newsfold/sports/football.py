"""Upcoming/finished matches via football-data.org, filtered to followed teams.

Fetches by competition code only (`/v4/competitions/{code}/matches`) — no team-ID
lookup needed — then keeps matches involving any configured team name
(word-boundary match, case-insensitive — see `sports/matching.py`). Free tier:
https://www.football-data.org/client/register.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from newsfold.ingest.common import make_client
from newsfold.sports.matching import matches_team
from newsfold.sports.models import Event, EventStatus
from newsfold.timeutil import to_utc_naive, utcnow

_BASE_URL = "https://api.football-data.org/v4"
_DEFAULT_API_KEY_ENV = "NEWSFOLD_SPORTS_FOOTBALL_API_KEY"
_LIVE_STATUSES = {"IN_PLAY", "PAUSED"}


@dataclass(frozen=True)
class FootballResult:
    ok: bool
    events: tuple[Event, ...] = ()
    error: str | None = None


@dataclass
class _Fetched:
    events: list[Event] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _fetch_competition_matches(
    client: httpx.Client, competition: str, api_key: str
) -> list[dict[str, Any]]:
    resp = client.get(
        f"{_BASE_URL}/competitions/{competition}/matches",
        headers={"X-Auth-Token": api_key},
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return list(data.get("matches") or [])


def _matches_team(match: dict[str, Any], teams: tuple[str, ...]) -> bool:
    if not teams:
        return True
    home = str((match.get("homeTeam") or {}).get("name") or "")
    away = str((match.get("awayTeam") or {}).get("name") or "")
    return any(matches_team(t, home, away) for t in teams)


def _to_event(match: dict[str, Any], competition_name: str) -> Event:
    home = str((match.get("homeTeam") or {}).get("name") or "?")
    away = str((match.get("awayTeam") or {}).get("name") or "?")
    raw_status = str(match.get("status") or "").upper()
    status: EventStatus = (
        "finished"
        if raw_status == "FINISHED"
        else "live" if raw_status in _LIVE_STATUSES else "scheduled"
    )
    full_time = ((match.get("score") or {}).get("fullTime")) or {}
    result = None
    if (
        status == "finished"
        and full_time.get("home") is not None
        and full_time.get("away") is not None
    ):
        result = f"{home} {full_time['home']}–{full_time['away']} {away}"
    kickoff = match.get("utcDate")
    start_time = _parse_iso(kickoff) if kickoff else None
    return Event(
        sport="football",
        title=f"{home} vs {away}",
        subtitle=competition_name,
        start_time=start_time,
        status=status,
        result=result,
    )


def _parse_iso(value: str) -> datetime | None:
    try:
        return to_utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _within_window(start_time: datetime | None, *, results_days: int, upcoming_days: int) -> bool:
    if start_time is None:
        return True
    now = utcnow()
    return now - timedelta(days=results_days) <= start_time <= now + timedelta(days=upcoming_days)


def fetch_football(cfg: dict[str, Any], *, client: httpx.Client | None = None) -> FootballResult:
    football_cfg = cfg.get("football") or {}
    competitions = tuple(football_cfg.get("competitions") or ())
    teams = tuple(football_cfg.get("teams") or ())
    if not competitions:
        return FootballResult(ok=True, events=())

    api_key_env = football_cfg.get("api_key_env") or _DEFAULT_API_KEY_ENV
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        return FootballResult(
            ok=False, error=f"missing football API key (${api_key_env}, see .env.example)"
        )

    results_days = int(cfg.get("results_days", 7))
    upcoming_days = int(cfg.get("upcoming_days", 14))

    owns_client = client is None
    active_client = client or make_client(float(cfg.get("http_timeout_sec", 20)))
    fetched = _Fetched()
    try:
        for competition in competitions:
            try:
                matches = _fetch_competition_matches(active_client, competition, api_key)
            except httpx.HTTPError as exc:
                fetched.errors.append(f"{competition}: {exc}")
                continue
            for match in matches:
                if not _matches_team(match, teams):
                    continue
                competition_name = str((match.get("competition") or {}).get("name") or competition)
                event = _to_event(match, competition_name)
                if _within_window(
                    event.start_time, results_days=results_days, upcoming_days=upcoming_days
                ):
                    fetched.events.append(event)
    finally:
        if owns_client:
            active_client.close()

    return FootballResult(
        ok=not fetched.errors or bool(fetched.events),
        events=tuple(fetched.events),
        error="; ".join(fetched.errors) or None,
    )

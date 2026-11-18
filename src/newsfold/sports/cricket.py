"""Upcoming/finished matches via Big Balls Sports Data's cricket API
(https://bigballsdata.com), filtered to followed teams.

Replaces the earlier CricAPI-based implementation. CricAPI's `matches` endpoint has no date or team
filter and isn't sorted, so a bounded page scan could — and did — miss a
followed team's match entirely. Big Balls' `/v1/cricket/matches` has no team
filter either (confirmed against its OpenAPI spec: only `series`, `date`,
`limit`/`offset`/`page` are real params — `team`/`search`/`q` are silently
ignored, not filters), but it *does* support `?date=YYYY-MM-DD` for one UTC
calendar day. We scan every day in `[now-results_days, now+upcoming_days]`
explicitly (one request per day) and get a *complete* match list for each day
— not a sample — then filter client-side by team name. Free tier is
1,000 req/day, so the default 21-day window costs ~21 requests per refresh.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from newsfold.ingest.common import make_client
from newsfold.sports.matching import matches_team
from newsfold.sports.models import Event, EventStatus
from newsfold.timeutil import to_utc_naive, utcnow

_URL = "https://api.bigballsdata.com/v1/cricket/matches"
_DEFAULT_API_KEY_ENV = "NEWSFOLD_SPORTS_CRICKET_API_KEY"


@dataclass(frozen=True)
class CricketResult:
    ok: bool
    events: tuple[Event, ...] = ()
    error: str | None = None


def _error_message(exc: httpx.HTTPStatusError) -> str:
    try:
        body: dict[str, Any] = exc.response.json()
        message = (body.get("error") or {}).get("message")
        return str(message) if message else str(exc)
    except ValueError:
        return str(exc)


def _fetch_matches_for_date(client: httpx.Client, api_key: str, day: str) -> list[dict[str, Any]]:
    resp = client.get(
        _URL,
        headers={"Authorization": f"Bearer {api_key}"},
        params={"date": day, "limit": 200},
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return list(data.get("data") or [])


def _date_range(*, results_days: int, upcoming_days: int) -> list[str]:
    today = utcnow().date()
    past = [(today - timedelta(days=d)).isoformat() for d in range(results_days, 0, -1)]
    future = [(today + timedelta(days=d)).isoformat() for d in range(upcoming_days + 1)]
    return past + future


def _matches_team(match: dict[str, Any], teams: tuple[str, ...]) -> bool:
    home = str((match.get("home") or {}).get("name") or "")
    away = str((match.get("away") or {}).get("name") or "")
    return any(matches_team(t, home, away) for t in teams)


def _parse_kickoff(value: str) -> datetime | None:
    try:
        return to_utc_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _to_event(match: dict[str, Any]) -> Event:
    home = str((match.get("home") or {}).get("name") or "?")
    away = str((match.get("away") or {}).get("name") or "?")
    raw_status = str(match.get("status") or "")
    status: EventStatus = (
        "finished" if raw_status == "finished" else "live" if raw_status == "live" else "scheduled"
    )
    score = match.get("score") or {}
    result = None
    if status == "finished" and score.get("home") is not None and score.get("away") is not None:
        result = f"{home} {score['home']} – {away} {score['away']}"
    kickoff = match.get("kickoff_utc")
    return Event(
        sport="cricket",
        title=f"{home} vs {away}",
        subtitle=str(match.get("league") or ""),
        start_time=_parse_kickoff(kickoff) if kickoff else None,
        status=status,
        result=result,
    )


def fetch_cricket(cfg: dict[str, Any], *, client: httpx.Client | None = None) -> CricketResult:
    cricket_cfg = cfg.get("cricket") or {}
    teams = tuple(cricket_cfg.get("teams") or ())
    if not teams:
        return CricketResult(ok=True, events=())

    api_key_env = cricket_cfg.get("api_key_env") or _DEFAULT_API_KEY_ENV
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        return CricketResult(
            ok=False, error=f"missing cricket API key (${api_key_env}, see .env.example)"
        )

    results_days = int(cfg.get("results_days", 7))
    upcoming_days = int(cfg.get("upcoming_days", 14))

    owns_client = client is None
    active_client = client or make_client(float(cfg.get("http_timeout_sec", 20)))
    events: list[Event] = []
    errors: list[str] = []
    try:
        for day in _date_range(results_days=results_days, upcoming_days=upcoming_days):
            try:
                matches = _fetch_matches_for_date(active_client, api_key, day)
            except httpx.HTTPStatusError as exc:
                errors.append(f"{day}: {_error_message(exc)}")
                continue
            except httpx.HTTPError as exc:
                errors.append(f"{day}: {exc}")
                continue
            events.extend(_to_event(m) for m in matches if _matches_team(m, teams))
    finally:
        if owns_client:
            active_client.close()

    if not events and errors:
        return CricketResult(ok=False, error="; ".join(errors))
    return CricketResult(ok=True, events=tuple(events), error="; ".join(errors) or None)

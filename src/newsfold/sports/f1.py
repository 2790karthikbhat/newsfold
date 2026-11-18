"""F1 calendar + latest race result via Jolpica-F1 (api.jolpi.ca), the free
Ergast-API-compatible successor — no key needed. There's only one championship,
so there's nothing to filter: the full remaining-season schedule plus one event
for the most recently completed race's result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from newsfold.ingest.common import make_client
from newsfold.sports.models import Event
from newsfold.timeutil import to_utc_naive, utcnow

_SCHEDULE_URL = "https://api.jolpi.ca/ergast/f1/current.json"
_LAST_RESULTS_URL = "https://api.jolpi.ca/ergast/f1/current/last/results.json"


@dataclass(frozen=True)
class F1Result:
    ok: bool
    events: tuple[Event, ...] = ()
    error: str | None = None


def _fetch_schedule(client: httpx.Client) -> list[dict[str, Any]]:
    resp = client.get(_SCHEDULE_URL)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    races: list[dict[str, Any]] = data.get("MRData", {}).get("RaceTable", {}).get("Races") or []
    return races


def _fetch_last_result(client: httpx.Client) -> dict[str, Any] | None:
    resp = client.get(_LAST_RESULTS_URL)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    races: list[dict[str, Any]] = data.get("MRData", {}).get("RaceTable", {}).get("Races") or []
    return races[0] if races else None


def _race_start(race: dict[str, Any]) -> datetime | None:
    date = race.get("date")
    if not date:
        return None
    time = race.get("time") or "00:00:00Z"
    try:
        return to_utc_naive(datetime.fromisoformat(f"{date}T{time}".replace("Z", "+00:00")))
    except ValueError:
        return None


def _to_schedule_event(race: dict[str, Any]) -> Event:
    return Event(
        sport="f1",
        title=str(race.get("raceName") or "Grand Prix"),
        subtitle=str((race.get("Circuit") or {}).get("circuitName") or ""),
        start_time=_race_start(race),
        status="scheduled",
        result=None,
    )


def _to_result_event(race: dict[str, Any]) -> Event:
    results = race.get("Results") or []
    winner = results[0] if results else None
    result = None
    if winner is not None:
        name = f"{winner['Driver']['givenName']} {winner['Driver']['familyName']}"
        team = winner.get("Constructor", {}).get("name", "")
        result = f"Winner: {name} ({team})" if team else f"Winner: {name}"
    return Event(
        sport="f1",
        title=f"{race.get('raceName', 'Grand Prix')} — Result",
        subtitle=str((race.get("Circuit") or {}).get("circuitName") or ""),
        start_time=_race_start(race),
        status="finished",
        result=result,
    )


def fetch_f1(cfg: dict[str, Any], *, client: httpx.Client | None = None) -> F1Result:
    f1_cfg = cfg.get("f1") or {}
    if not f1_cfg.get("enabled", True):
        return F1Result(ok=True, events=())

    owns_client = client is None
    active_client = client or make_client(float(cfg.get("http_timeout_sec", 20)))
    try:
        try:
            races = _fetch_schedule(active_client)
            last_race = _fetch_last_result(active_client)
        except httpx.HTTPError as exc:
            return F1Result(ok=False, error=str(exc))
    finally:
        if owns_client:
            active_client.close()

    now = utcnow()
    events = [
        _to_schedule_event(r)
        for r in races
        if (start := _race_start(r)) is not None and start >= now
    ]
    if last_race is not None:
        events.append(_to_result_event(last_race))
    return F1Result(ok=True, events=tuple(events))

from __future__ import annotations

from datetime import datetime, timedelta

import newsfold.sports.f1 as f1_mod
from newsfold.sports.f1 import fetch_f1
from newsfold.timeutil import utcnow

_FUTURE = (utcnow() + timedelta(days=10)).date().isoformat()
_PAST = (utcnow() - timedelta(days=10)).date().isoformat()


def _race(date: str, name: str = "Italian Grand Prix") -> dict:
    return {
        "raceName": name,
        "date": date,
        "time": "13:00:00Z",
        "Circuit": {"circuitName": "Autodromo Nazionale Monza"},
    }


_LAST_RESULT = {
    **_race(_PAST, "Dutch Grand Prix"),
    "Results": [
        {
            "Driver": {"givenName": "Max", "familyName": "Verstappen"},
            "Constructor": {"name": "Red Bull"},
        }
    ],
}


def test_fetch_f1_keeps_only_future_races_from_schedule(monkeypatch) -> None:
    monkeypatch.setattr(
        f1_mod, "_fetch_schedule", lambda client: [_race(_FUTURE), _race(_PAST, "Old Grand Prix")]
    )
    monkeypatch.setattr(f1_mod, "_fetch_last_result", lambda client: None)

    result = fetch_f1({"f1": {"enabled": True}})

    assert result.ok
    assert len(result.events) == 1
    assert result.events[0].status == "scheduled"
    assert result.events[0].title == "Italian Grand Prix"


def test_fetch_f1_appends_last_result_event(monkeypatch) -> None:
    monkeypatch.setattr(f1_mod, "_fetch_schedule", lambda client: [])
    monkeypatch.setattr(f1_mod, "_fetch_last_result", lambda client: _LAST_RESULT)

    result = fetch_f1({"f1": {"enabled": True}})

    assert len(result.events) == 1
    event = result.events[0]
    assert event.status == "finished"
    assert event.result == "Winner: Max Verstappen (Red Bull)"


def test_fetch_f1_disabled_is_a_noop() -> None:
    result = fetch_f1({"f1": {"enabled": False}})

    assert result.ok
    assert result.events == ()


def test_fetch_f1_provider_error_is_reported_not_raised(monkeypatch) -> None:
    import httpx

    def boom(client):
        raise httpx.HTTPError("nope")

    monkeypatch.setattr(f1_mod, "_fetch_schedule", boom)

    result = fetch_f1({"f1": {"enabled": True}})

    assert not result.ok
    assert result.events == ()


def test_race_start_parses_date_and_time() -> None:
    start = f1_mod._race_start(_race(_FUTURE))
    assert isinstance(start, datetime)
    assert start.tzinfo is None

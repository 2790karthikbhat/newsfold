from __future__ import annotations

from datetime import timedelta

import httpx

import newsfold.sports.cricket as cricket_mod
from newsfold.sports.cricket import fetch_cricket
from newsfold.timeutil import utcnow

_NOW = utcnow()


def _match(
    home: str,
    away: str,
    *,
    finished: bool,
    home_score=None,
    away_score=None,
    status: str | None = None,
) -> dict:
    return {
        "home": {"id": "h1", "name": home, "short_name": home[:4].upper()},
        "away": {"id": "a1", "name": away, "short_name": away[:4].upper()},
        "league": "Test Series",
        "kickoff_utc": _NOW.isoformat() + "Z",
        "status": status or ("finished" if finished else "scheduled"),
        "score": {"home": home_score, "away": away_score} if finished else None,
    }


_MATCH_RCB = _match(
    "Royal Challengers Bangalore", "Mumbai Indians", finished=True, home_score=180, away_score=165
)
_MATCH_OTHER = _match("England", "Australia", finished=False)


def _cfg(**overrides: object) -> dict:
    base: dict = {
        "results_days": 2,
        "upcoming_days": 2,
        "cricket": {
            "api_key_env": "TEST_CRICKET_KEY",
            "teams": ["India", "Royal Challengers Bangalore"],
        },
    }
    base.update(overrides)
    return base


def test_fetch_cricket_scans_the_window_and_filters_to_configured_teams(monkeypatch) -> None:
    monkeypatch.setenv("TEST_CRICKET_KEY", "secret")
    seen_days: list[str] = []

    def fake(client, api_key, day):
        seen_days.append(day)
        return [_MATCH_RCB, _MATCH_OTHER]

    monkeypatch.setattr(cricket_mod, "_fetch_matches_for_date", fake)

    result = fetch_cricket(_cfg())

    # results_days=2, upcoming_days=2 -> 5 calendar days scanned
    assert len(seen_days) == 5
    assert len(seen_days) == len(set(seen_days))  # each day fetched once
    # RCB match matched on every day scanned; England/Australia never matches
    assert len(result.events) == 5
    event = result.events[0]
    assert event.sport == "cricket"
    assert event.status == "finished"
    assert event.result == "Royal Challengers Bangalore 180 – Mumbai Indians 165"
    assert result.ok


def test_fetch_cricket_live_status_maps_to_live(monkeypatch) -> None:
    monkeypatch.setenv("TEST_CRICKET_KEY", "secret")
    live_match = _match(
        "Royal Challengers Bangalore", "Chennai Super Kings", finished=False, status="live"
    )
    monkeypatch.setattr(
        cricket_mod, "_fetch_matches_for_date", lambda client, api_key, day: [live_match]
    )

    result = fetch_cricket(_cfg())

    assert result.events
    assert all(e.status == "live" for e in result.events)
    assert all(e.result is None for e in result.events)


def test_fetch_cricket_no_teams_configured_is_a_noop() -> None:
    result = fetch_cricket({"cricket": {}})

    assert result.ok
    assert result.events == ()


def test_fetch_cricket_missing_key_errors(monkeypatch) -> None:
    monkeypatch.delenv("TEST_CRICKET_KEY", raising=False)

    result = fetch_cricket(_cfg())

    assert not result.ok
    assert result.error is not None and "TEST_CRICKET_KEY" in result.error


def test_fetch_cricket_all_days_failing_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setenv("TEST_CRICKET_KEY", "secret")

    def boom(client, api_key, day):
        raise httpx.HTTPError("nope")

    monkeypatch.setattr(cricket_mod, "_fetch_matches_for_date", boom)

    result = fetch_cricket(_cfg())

    assert not result.ok
    assert result.events == ()


def test_fetch_cricket_one_day_failing_keeps_the_others(monkeypatch) -> None:
    """A single day's request failing (rate limit, transient 5xx) shouldn't
    discard matches already found on the days that succeeded."""
    monkeypatch.setenv("TEST_CRICKET_KEY", "secret")
    calls = {"n": 0}

    def flaky(client, api_key, day):
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.HTTPError("transient")
        return [_MATCH_RCB]

    monkeypatch.setattr(cricket_mod, "_fetch_matches_for_date", flaky)

    result = fetch_cricket(_cfg())

    assert result.ok
    assert result.error is not None and "transient" in result.error
    assert len(result.events) == 4  # 5 days scanned, 1 failed


def test_date_range_spans_results_days_through_upcoming_days() -> None:
    days = cricket_mod._date_range(results_days=2, upcoming_days=2)

    assert len(days) == 5
    today = utcnow().date()
    assert days[0] == (today - timedelta(days=2)).isoformat()
    assert days[-1] == (today + timedelta(days=2)).isoformat()
    assert days == sorted(days)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


def test_fetch_matches_for_date_sends_bearer_auth_and_date_param() -> None:
    captured = {}

    class _Client:
        def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return _FakeResponse(200, {"data": [_MATCH_RCB]})

    matches = cricket_mod._fetch_matches_for_date(_Client(), "secret-key", "2026-09-15")

    assert matches == [_MATCH_RCB]
    assert captured["headers"] == {"Authorization": "Bearer secret-key"}
    assert captured["params"] == {"date": "2026-09-15", "limit": 200}


def test_error_message_extracts_api_error_body() -> None:
    resp = _FakeResponse(401, {"error": {"code": "unauthorized", "message": "invalid API key"}})
    exc = httpx.HTTPStatusError("401", request=None, response=resp)  # type: ignore[arg-type]

    assert cricket_mod._error_message(exc) == "invalid API key"


def test_matches_team_checks_home_and_away_names() -> None:
    assert cricket_mod._matches_team(_MATCH_RCB, ("India",)) is False
    assert cricket_mod._matches_team(_MATCH_RCB, ("Royal Challengers Bangalore",)) is True
    assert cricket_mod._matches_team(_MATCH_RCB, ("Mumbai Indians",)) is True

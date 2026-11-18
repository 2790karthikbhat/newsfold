from __future__ import annotations

from datetime import timedelta

import httpx

import newsfold.sports.football as football_mod
from newsfold.sports.football import fetch_football
from newsfold.timeutil import utcnow

_NOW = utcnow()


def _cfg(**overrides: object) -> dict:
    base: dict = {
        "results_days": 7,
        "upcoming_days": 14,
        "football": {
            "api_key_env": "TEST_FOOTBALL_KEY",
            "competitions": ["PL"],
            "teams": ["Manchester United"],
        },
    }
    base.update(overrides)
    return base


def _match(
    home: str,
    away: str,
    *,
    days_from_now: float,
    finished: bool,
    home_score=None,
    away_score=None,
    status: str | None = None,
) -> dict:
    when = _NOW.replace(microsecond=0) + timedelta(days=days_from_now)
    return {
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "status": status or ("FINISHED" if finished else "SCHEDULED"),
        "score": {"fullTime": {"home": home_score, "away": away_score}},
        "utcDate": when.isoformat().replace("+00:00", "Z"),
        "competition": {"name": "Premier League"},
    }


_MATCH_UNITED_RECENT = _match(
    "Manchester United FC",
    "Liverpool FC",
    days_from_now=-2,
    finished=True,
    home_score=2,
    away_score=1,
)
_MATCH_UNITED_TOO_OLD = _match(
    "Manchester United FC",
    "Everton FC",
    days_from_now=-30,
    finished=True,
    home_score=1,
    away_score=0,
)
_MATCH_OTHER = _match("Arsenal FC", "Chelsea FC", days_from_now=5, finished=False)


def test_fetch_football_filters_to_configured_team_within_the_window(monkeypatch) -> None:
    monkeypatch.setenv("TEST_FOOTBALL_KEY", "secret")
    monkeypatch.setattr(
        football_mod,
        "_fetch_competition_matches",
        lambda client, competition, api_key: [
            _MATCH_UNITED_RECENT,
            _MATCH_UNITED_TOO_OLD,
            _MATCH_OTHER,
        ],
    )

    result = fetch_football(_cfg())

    assert result.ok
    assert len(result.events) == 1
    event = result.events[0]
    assert event.sport == "football"
    assert "Manchester United" in event.title
    assert event.status == "finished"
    assert event.result == "Manchester United FC 2–1 Liverpool FC"


def test_fetch_football_empty_teams_shows_whole_competition(monkeypatch) -> None:
    monkeypatch.setenv("TEST_FOOTBALL_KEY", "secret")
    monkeypatch.setattr(
        football_mod,
        "_fetch_competition_matches",
        lambda client, competition, api_key: [_MATCH_UNITED_RECENT, _MATCH_OTHER],
    )

    result = fetch_football(
        _cfg(football={"api_key_env": "TEST_FOOTBALL_KEY", "competitions": ["PL"]})
    )

    assert result.ok
    assert len(result.events) == 2


def test_fetch_football_missing_key_errors(monkeypatch) -> None:
    monkeypatch.delenv("TEST_FOOTBALL_KEY", raising=False)

    result = fetch_football(_cfg())

    assert not result.ok
    assert result.events == ()
    assert result.error is not None and "TEST_FOOTBALL_KEY" in result.error


def test_fetch_football_no_competitions_configured_is_a_noop() -> None:
    result = fetch_football({"football": {}})

    assert result.ok
    assert result.events == ()


def test_fetch_football_in_play_match_maps_to_live_status(monkeypatch) -> None:
    monkeypatch.setenv("TEST_FOOTBALL_KEY", "secret")
    live_match = _match(
        "Manchester United FC", "Arsenal FC", days_from_now=0, finished=False, status="IN_PLAY"
    )
    monkeypatch.setattr(
        football_mod,
        "_fetch_competition_matches",
        lambda client, competition, api_key: [live_match],
    )

    result = fetch_football(_cfg())

    assert len(result.events) == 1
    assert result.events[0].status == "live"
    assert result.events[0].result is None  # no fullTime score to report while live


def test_fetch_football_provider_error_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setenv("TEST_FOOTBALL_KEY", "secret")

    def boom(client, competition, api_key):
        raise httpx.HTTPError("nope")

    monkeypatch.setattr(football_mod, "_fetch_competition_matches", boom)

    result = fetch_football(_cfg())

    assert not result.ok
    assert result.events == ()
    assert result.error is not None and "PL" in result.error

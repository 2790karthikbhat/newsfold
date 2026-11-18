from __future__ import annotations

import newsfold.config as config_mod
import newsfold.sports.aggregate as aggregate_mod
import newsfold.sports.cache as cache_mod
from newsfold.config import Settings
from newsfold.sports.cricinfo import CricinfoResult
from newsfold.sports.cricket import CricketResult
from newsfold.sports.f1 import F1Result
from newsfold.sports.football import FootballResult
from newsfold.sports.models import Event

_EVENT = Event(
    sport="football", title="A vs B", subtitle="", start_time=None, status="scheduled", result=None
)


def _fake_settings() -> Settings:
    return Settings(db_url="sqlite:///:memory:", target_language="en", raw={"sports": {}})


def test_refresh_all_combines_providers_and_survives_one_failing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path / "sports_cache")
    monkeypatch.setattr(config_mod, "load_settings", _fake_settings)
    monkeypatch.setattr(aggregate_mod, "load_settings", _fake_settings)
    monkeypatch.setattr(
        aggregate_mod, "fetch_football", lambda cfg: FootballResult(ok=True, events=(_EVENT,))
    )
    monkeypatch.setattr(
        aggregate_mod, "fetch_cricket", lambda cfg: CricketResult(ok=False, error="boom")
    )
    _LIVE_EVENT = Event(
        sport="cricket",
        title="X vs Y",
        subtitle="Live now",
        start_time=None,
        status="live",
        result=None,
    )
    monkeypatch.setattr(
        aggregate_mod,
        "fetch_live_cricket",
        lambda cfg: CricinfoResult(ok=True, events=(_LIVE_EVENT,)),
    )
    monkeypatch.setattr(aggregate_mod, "fetch_f1", lambda cfg: F1Result(ok=True, events=()))

    report = aggregate_mod.refresh_all()

    assert report.football_count == 1
    assert report.cricket_count == 1  # the live event, cricket_result itself errored
    assert report.errors == ["cricket: boom"]

    snapshot = aggregate_mod.load_cached()
    assert snapshot is not None
    assert snapshot.events == [_EVENT, _LIVE_EVENT]
    assert snapshot.stale is False


def test_refresh_all_dry_run_does_not_call_providers(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path / "sports_cache")

    def fail(cfg):
        raise AssertionError("should not be called in dry-run")

    monkeypatch.setattr(aggregate_mod, "fetch_football", fail)
    monkeypatch.setattr(aggregate_mod, "fetch_cricket", fail)
    monkeypatch.setattr(aggregate_mod, "fetch_live_cricket", fail)
    monkeypatch.setattr(aggregate_mod, "fetch_f1", fail)

    report = aggregate_mod.refresh_all(dry_run=True)

    assert report.football_count == 0
    assert report.errors == []


def test_load_cached_returns_none_when_never_fetched(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path / "sports_cache")
    monkeypatch.setattr(aggregate_mod, "load_settings", _fake_settings)

    assert aggregate_mod.load_cached() is None

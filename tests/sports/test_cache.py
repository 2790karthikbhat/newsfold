from __future__ import annotations

from datetime import timedelta

import newsfold.sports.cache as cache_mod
from newsfold.sports.models import Event
from newsfold.timeutil import utcnow

_EVENT = Event(
    sport="f1",
    title="Italian Grand Prix",
    subtitle="Monza",
    start_time=utcnow(),
    status="scheduled",
    result=None,
)


def test_cache_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path / "sports_cache")

    assert cache_mod.read_cache("sports") is None

    cache_mod.write_cache("sports", [_EVENT])
    result = cache_mod.read_cache("sports")

    assert result is not None
    events, fetched_at = result
    assert events == [_EVENT]
    assert (utcnow() - fetched_at).total_seconds() < 5


def test_is_stale(monkeypatch) -> None:
    fresh = utcnow()
    old = utcnow() - timedelta(hours=2)

    assert cache_mod.is_stale(old, ttl_minutes=60) is True
    assert cache_mod.is_stale(fresh, ttl_minutes=60) is False

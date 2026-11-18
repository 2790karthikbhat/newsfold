from __future__ import annotations

import httpx

import newsfold.sports.cricinfo as cricinfo_mod
from newsfold.sports.cricinfo import fetch_live_cricket

_SAMPLE_TITLES = [
    "Maiwand Champions 227/10  v Hindukush Strikers 174/3 *",
    "Pamir Legends 451/8  v Mah-e-Par Stars 315/6 &  126/10 *",
    "Guyana Amazon Warriors 206/4  v Jamaica Kingsmen 165/3 *",
    "Queensland v Victoria",
    "England v Sri Lanka",
    "India 180/4  v Afghanistan 120/6 *",
]


def _cfg(**overrides: object) -> dict:
    base: dict = {"cricket": {"teams": ["India", "Royal Challengers Bangalore"]}}
    base.update(overrides)
    return base


def test_fetch_live_cricket_filters_to_configured_teams(monkeypatch) -> None:
    monkeypatch.setattr(cricinfo_mod, "_fetch_titles", lambda client: _SAMPLE_TITLES)

    result = fetch_live_cricket(_cfg())

    assert result.ok
    assert len(result.events) == 1
    event = result.events[0]
    assert event.sport == "cricket"
    assert event.status == "live"
    assert event.start_time is None
    assert event.result is None
    assert event.subtitle == "Live now"
    assert event.title == "India 180/4  v Afghanistan 120/6 *"


def test_fetch_live_cricket_does_not_false_positive_on_substring(monkeypatch) -> None:
    """ "India" must not match inside "Mumbai Indians" (sports/matching.py already
    guards this — this just proves the live feed goes through the same matcher)."""
    monkeypatch.setattr(
        cricinfo_mod,
        "_fetch_titles",
        lambda client: ["Mumbai Indians 100/2  v Delhi Capitals 50/1 *"],
    )

    result = fetch_live_cricket(_cfg())

    assert result.events == ()


def test_fetch_live_cricket_skips_titles_without_a_v_separator(monkeypatch) -> None:
    monkeypatch.setattr(cricinfo_mod, "_fetch_titles", lambda client: ["India tour of England"])

    result = fetch_live_cricket(_cfg())

    assert result.ok
    assert result.events == ()


def test_fetch_live_cricket_no_teams_configured_is_a_noop() -> None:
    result = fetch_live_cricket({"cricket": {}})

    assert result.ok
    assert result.events == ()


def test_fetch_live_cricket_provider_error_is_reported_not_raised(monkeypatch) -> None:
    def boom(client):
        raise httpx.HTTPError("nope")

    monkeypatch.setattr(cricinfo_mod, "_fetch_titles", boom)

    result = fetch_live_cricket(_cfg())

    assert not result.ok
    assert result.events == ()


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def get(self, url):
        return _FakeResponse(self._body)


_SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
<title>Cricinfo Live Scores</title>
<item>
<title>India 180/4  v Afghanistan 120/6 *</title>
<link>http://www.cricinfo.com/ci/engine/match/1.html</link>
<guid>http://www.cricinfo.com/ci/engine/match/1.html</guid>
</item>
</channel>
</rss>
"""


def test_fetch_titles_parses_real_rss_shape() -> None:
    titles = cricinfo_mod._fetch_titles(_FakeClient(_SAMPLE_FEED))

    assert titles == ["India 180/4  v Afghanistan 120/6 *"]

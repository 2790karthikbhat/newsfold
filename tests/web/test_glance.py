from __future__ import annotations

import calendar
import json
from datetime import timedelta

import newsfold.web.routes.glance as glance_routes
from newsfold.config import Settings
from newsfold.models import Article, ArticleStatus, SourceType, Topic, TopicMode, TopicStatus
from newsfold.sports.aggregate import SportsSnapshot
from newsfold.sports.models import Event
from newsfold.timeutil import utcnow


def _settings_with_public_base_url(value: str | None) -> Settings:
    web_section: dict[str, str] = {"public_base_url": value} if value else {}
    return Settings(db_url="sqlite:///:memory:", target_language="en", raw={"web": web_section})


def _make_topic_with_article(db, *, published_at=None) -> int:
    topic = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(topic)
    db.flush()
    article = Article(
        topic_id=topic.id,
        source_url="https://example.com/a",
        source_name="Example",
        source_type=SourceType.user_source,
        title_original="Verstappen wins again",
        status=ArticleStatus.clustered,
        published_at=published_at,
    )
    db.add(article)
    db.commit()
    return article.id


def test_glance_digest_falls_back_to_request_host_when_unconfigured(
    client, db, monkeypatch
) -> None:
    monkeypatch.setattr(
        glance_routes, "load_settings", lambda: _settings_with_public_base_url(None)
    )
    article_id = _make_topic_with_article(db)

    resp = client.get("/glance/digest")

    assert resp.status_code == 200
    assert "<html" not in resp.text
    assert "<aside" not in resp.text
    assert "Verstappen wins again" in resp.text
    assert f"/digest/article/{article_id}" in resp.text
    assert str(client.base_url) in resp.text


def test_glance_digest_uses_configured_public_base_url(client, db, monkeypatch) -> None:
    monkeypatch.setattr(
        glance_routes,
        "load_settings",
        lambda: _settings_with_public_base_url("http://192.168.1.5:8765"),
    )
    _make_topic_with_article(db)

    resp = client.get("/glance/digest")

    assert "http://192.168.1.5:8765/digest/article/" in resp.text
    assert "http://testserver" not in resp.text


def test_glance_digest_sets_widget_headers(client, db, monkeypatch) -> None:
    monkeypatch.setattr(
        glance_routes,
        "load_settings",
        lambda: _settings_with_public_base_url("http://192.168.1.5:8765"),
    )
    _make_topic_with_article(db)

    resp = client.get("/glance/digest")

    assert resp.headers["Widget-Title"] == "Newsfold"
    assert resp.headers["Widget-Title-URL"] == "http://192.168.1.5:8765/digest"
    assert resp.headers["Widget-Content-Type"] == "html"


def test_glance_digest_includes_relative_time_for_published_articles(
    client, db, monkeypatch
) -> None:
    monkeypatch.setattr(
        glance_routes, "load_settings", lambda: _settings_with_public_base_url(None)
    )
    now = utcnow().replace(microsecond=0)
    article_id = _make_topic_with_article(db, published_at=now)

    resp = client.get("/glance/digest")

    expected_epoch = calendar.timegm(now.timetuple())
    assert f'data-dynamic-relative-time="{expected_epoch}"' in resp.text
    assert f"/digest/article/{article_id}" in resp.text


def test_glance_digest_omits_relative_time_attribute_when_unpublished(
    client, db, monkeypatch
) -> None:
    monkeypatch.setattr(
        glance_routes, "load_settings", lambda: _settings_with_public_base_url(None)
    )
    _make_topic_with_article(db)  # no published_at

    resp = client.get("/glance/digest")

    assert "data-dynamic-relative-time" not in resp.text


def test_glance_digest_empty_state(client, monkeypatch) -> None:
    monkeypatch.setattr(
        glance_routes, "load_settings", lambda: _settings_with_public_base_url(None)
    )

    resp = client.get("/glance/digest")

    assert resp.status_code == 200
    assert "No unread stories" in resp.text


def test_glance_sports_empty_state_when_never_fetched(client, monkeypatch) -> None:
    monkeypatch.setattr(glance_routes, "load_cached", lambda: None)

    resp = client.get("/glance/sports")

    assert resp.status_code == 200
    assert "<html" not in resp.text
    assert "digest sports fetch" in resp.text


def test_glance_sports_renders_cached_events(client, monkeypatch) -> None:
    now = utcnow().replace(microsecond=0)
    snapshot = SportsSnapshot(
        events=[
            Event(
                sport="f1",
                title="Italian Grand Prix",
                subtitle="Monza",
                start_time=now,
                status="scheduled",
                result=None,
            )
        ],
        fetched_at=utcnow(),
        stale=False,
    )
    monkeypatch.setattr(glance_routes, "load_cached", lambda: snapshot)

    resp = client.get("/glance/sports")

    assert resp.status_code == 200
    assert "Italian Grand Prix" in resp.text
    assert "(cached data may be stale)" not in resp.text
    expected_epoch = calendar.timegm(now.timetuple())
    assert f'data-dynamic-relative-time="{expected_epoch}"' in resp.text


def test_glance_sports_sets_widget_headers(client, monkeypatch) -> None:
    monkeypatch.setattr(glance_routes, "load_cached", lambda: None)

    resp = client.get("/glance/sports")

    assert resp.headers["Widget-Title"] == "Sports"
    assert resp.headers["Widget-Content-Type"] == "html"
    assert "Widget-Title-URL" not in resp.headers


def _mixed_sports_snapshot() -> SportsSnapshot:
    # +1h, not exactly "now" — _last_live_next filters "scheduled" to
    # start_time >= now, and by the time the route re-evaluates utcnow() a
    # fixture timestamped exactly "now" has already slipped into the past.
    soon = utcnow().replace(microsecond=0) + timedelta(hours=1)
    return SportsSnapshot(
        events=[
            Event(
                sport="football",
                title="Manchester United vs Liverpool",
                subtitle="Premier League",
                start_time=soon,
                status="scheduled",
                result=None,
            ),
            Event(
                sport="cricket",
                title="India vs Australia",
                subtitle="T20",
                start_time=soon,
                status="scheduled",
                result=None,
            ),
            Event(
                sport="f1",
                title="Italian Grand Prix",
                subtitle="Monza",
                start_time=soon,
                status="scheduled",
                result=None,
            ),
        ],
        fetched_at=utcnow(),
        stale=False,
    )


def test_glance_sports_filters_to_one_sport_per_tab(client, monkeypatch) -> None:
    monkeypatch.setattr(glance_routes, "load_cached", _mixed_sports_snapshot)

    resp = client.get("/glance/sports", params={"sport": "cricket"})

    assert resp.headers["Widget-Title"] == "Cricket"
    assert "India vs Australia" in resp.text
    assert "Manchester United" not in resp.text
    assert "Italian Grand Prix" not in resp.text


def test_glance_sports_no_sport_param_shows_everything(client, monkeypatch) -> None:
    monkeypatch.setattr(glance_routes, "load_cached", _mixed_sports_snapshot)

    resp = client.get("/glance/sports")

    assert "Manchester United" in resp.text
    assert "India vs Australia" in resp.text
    assert "Italian Grand Prix" in resp.text


def test_glance_sports_per_sport_empty_state_names_the_sport(client, monkeypatch) -> None:
    # f1 is not a "card" sport, so it still uses the flat-list empty state.
    monkeypatch.setattr(glance_routes, "load_cached", lambda: None)

    resp = client.get("/glance/sports", params={"sport": "f1"})

    assert "No F1 data cached yet" in resp.text


def test_glance_sports_card_layout_empty_state_when_never_fetched(client, monkeypatch) -> None:
    monkeypatch.setattr(glance_routes, "load_cached", lambda: None)

    resp = client.get("/glance/sports", params={"sport": "football"})

    assert resp.status_code == 200
    assert "No finished matches found" in resp.text
    assert "No matches are played right now" in resp.text
    assert "No upcoming matches found" in resp.text


def _football_last_live_next_snapshot() -> SportsSnapshot:
    now = utcnow().replace(microsecond=0)
    return SportsSnapshot(
        events=[
            Event(
                sport="football",
                title="AC Milan vs SS Lazio",
                subtitle="Serie A",
                start_time=now - timedelta(days=3),
                status="finished",
                result="AC Milan 1–0 SS Lazio",
            ),
            Event(
                sport="football",
                title="Napoli vs AS Roma",
                subtitle="Serie A",
                start_time=now,
                status="live",
                result=None,
            ),
            Event(
                sport="football",
                title="Torino FC vs AC Milan",
                subtitle="Serie A",
                start_time=now + timedelta(days=8),
                status="scheduled",
                result=None,
            ),
        ],
        fetched_at=utcnow(),
        stale=False,
    )


def test_glance_sports_card_layout_shows_last_live_next(client, monkeypatch) -> None:
    monkeypatch.setattr(glance_routes, "load_cached", _football_last_live_next_snapshot)

    resp = client.get("/glance/sports", params={"sport": "football"})

    assert resp.status_code == 200
    assert "<html" not in resp.text
    assert "AC Milan 1–0 SS Lazio" in resp.text  # last match: result, not "vs" title
    assert "Napoli vs AS Roma" in resp.text  # live match
    assert "Torino FC vs AC Milan" in resp.text  # next match
    assert "No finished matches found" not in resp.text
    assert "No matches are played right now" not in resp.text
    assert "No upcoming matches found" not in resp.text


def test_glance_sports_card_layout_not_used_for_f1(client, monkeypatch) -> None:
    monkeypatch.setattr(glance_routes, "load_cached", _mixed_sports_snapshot)

    resp = client.get("/glance/sports", params={"sport": "f1"})

    assert "Italian Grand Prix" in resp.text
    assert "Last match" not in resp.text
    assert "Live match" not in resp.text


def _event(**overrides) -> Event:
    base = {
        "sport": "football",
        "title": "A vs B",
        "subtitle": "",
        "start_time": utcnow(),
        "status": "scheduled",
        "result": None,
    }
    base.update(overrides)
    return Event(**base)


def test_last_live_next_picks_closest_finished_and_soonest_scheduled() -> None:
    now = utcnow().replace(microsecond=0)
    older_finished = _event(title="Old", status="finished", start_time=now - timedelta(days=10))
    recent_finished = _event(title="Recent", status="finished", start_time=now - timedelta(days=1))
    far_scheduled = _event(title="Far", status="scheduled", start_time=now + timedelta(days=20))
    soon_scheduled = _event(title="Soon", status="scheduled", start_time=now + timedelta(days=1))

    result = glance_routes._last_live_next(
        [older_finished, recent_finished, far_scheduled, soon_scheduled]
    )

    assert result["last_match"] is recent_finished
    assert result["next_match"] is soon_scheduled
    assert result["live_match"] is None


def test_last_live_next_picks_the_live_event() -> None:
    live = _event(title="Live now", status="live")

    result = glance_routes._last_live_next([live])

    assert result["live_match"] is live


def test_last_live_next_ignores_events_with_no_start_time() -> None:
    undated = _event(title="No date", status="finished", start_time=None)

    result = glance_routes._last_live_next([undated])

    assert result["last_match"] is None


def test_last_live_next_all_none_when_empty() -> None:
    result = glance_routes._last_live_next([])

    assert result == {"last_match": None, "live_match": None, "next_match": None}

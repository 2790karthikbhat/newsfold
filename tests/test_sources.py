from __future__ import annotations

import json

import pytest
from sqlalchemy import select

import newsfold.sources as sources_mod
from newsfold.ingest.user_sources import DiscoveryResult
from newsfold.models import Source, SourceStatus, Topic, TopicMode, TopicStatus
from newsfold.sources import SourceError, add_source, list_sources, remove_source


@pytest.fixture
def f1(db):
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    return t


def _stub_discovery(monkeypatch, result: DiscoveryResult) -> None:
    monkeypatch.setattr(sources_mod, "discover_feed", lambda *a, **k: result)


def test_add_source_active_with_topic(db, f1, monkeypatch) -> None:
    _stub_discovery(
        monkeypatch,
        DiscoveryResult(ok=True, feed_url="https://formula1.com/rss", feed_title="F1 News"),
    )

    result = add_source(db, "https://formula1.com", topic_keyword="F1")

    assert result.created
    src = result.source
    assert src.status == SourceStatus.active
    assert src.resolved_feed_url == "https://formula1.com/rss"
    assert src.name == "F1 News"
    assert src.topic_id == f1.id


def test_add_source_broken_when_no_feed(db, monkeypatch) -> None:
    _stub_discovery(monkeypatch, DiscoveryResult(ok=False, error="no feed found"))

    result = add_source(db, "https://no-feed.example")

    assert result.source.status == SourceStatus.broken
    assert result.source.name == "no-feed.example"
    assert result.source.resolved_feed_url is None


def test_add_source_unknown_topic_raises(db, monkeypatch) -> None:
    _stub_discovery(monkeypatch, DiscoveryResult(ok=True, feed_url="https://x/rss"))
    with pytest.raises(SourceError, match="unknown topic"):
        add_source(db, "https://x", topic_keyword="cricket")


def test_add_source_reruns_update_in_place(db, monkeypatch) -> None:
    _stub_discovery(monkeypatch, DiscoveryResult(ok=False, error="down"))
    add_source(db, "https://flaky.example")
    db.flush()

    _stub_discovery(monkeypatch, DiscoveryResult(ok=True, feed_url="https://flaky.example/feed"))
    result = add_source(db, "https://flaky.example")

    assert not result.created
    assert db.scalar(select(Source).where(Source.input_url == "https://flaky.example"))
    assert len(list_sources(db)) == 1
    assert result.source.status == SourceStatus.active


def test_remove_source(db, monkeypatch) -> None:
    _stub_discovery(monkeypatch, DiscoveryResult(ok=True, feed_url="https://x/rss"))
    result = add_source(db, "https://x")
    db.flush()

    assert remove_source(db, result.source.id) is True
    assert remove_source(db, 999) is False

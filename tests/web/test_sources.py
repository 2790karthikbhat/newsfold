from __future__ import annotations

import json

from sqlalchemy import select

import newsfold.sources as sources_mod
from newsfold.ingest.user_sources import DiscoveryResult
from newsfold.models import Source, Topic, TopicMode, TopicStatus


def _stub_discovery(monkeypatch, result: DiscoveryResult) -> None:
    monkeypatch.setattr(sources_mod, "discover_feed", lambda *a, **k: result)


def test_list_sources_empty(client) -> None:
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "No sources yet" in resp.text


def test_create_source_active(client, db, monkeypatch) -> None:
    _stub_discovery(
        monkeypatch,
        DiscoveryResult(ok=True, feed_url="https://formula1.com/rss", feed_title="F1 News"),
    )

    resp = client.post(
        "/sources",
        data={"url": "https://formula1.com", "name": "", "topic": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "msg=" in resp.headers["location"]
    db.expire_all()
    row = db.scalar(select(Source).where(Source.input_url == "https://formula1.com"))
    assert row is not None
    assert row.resolved_feed_url == "https://formula1.com/rss"


def test_create_source_broken_still_redirects_with_error(client, monkeypatch) -> None:
    _stub_discovery(monkeypatch, DiscoveryResult(ok=False, error="no feed found"))

    resp = client.post(
        "/sources",
        data={"url": "https://no-feed.example", "name": "", "topic": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_create_source_unknown_topic_redirects_with_error(client, monkeypatch) -> None:
    _stub_discovery(monkeypatch, DiscoveryResult(ok=True, feed_url="https://x/rss"))

    resp = client.post(
        "/sources",
        data={"url": "https://x", "name": "", "topic": "cricket"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_remove_source(client, db, monkeypatch) -> None:
    _stub_discovery(monkeypatch, DiscoveryResult(ok=True, feed_url="https://x/rss"))
    db.add(
        Topic(
            keyword="F1",
            mode=TopicMode.temporary,
            languages=json.dumps(["en"]),
            status=TopicStatus.active,
        )
    )
    db.commit()

    client.post("/sources", data={"url": "https://x", "name": "", "topic": ""})
    db.expire_all()
    source = db.scalar(select(Source).where(Source.input_url == "https://x"))
    assert source is not None

    resp = client.post(f"/sources/{source.id}/remove", follow_redirects=False)

    assert resp.status_code == 303
    assert "msg=removed" in resp.headers["location"]
    db.expunge_all()
    assert db.scalar(select(Source).where(Source.id == source.id)) is None

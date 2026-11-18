from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

import newsfold.ingest.fetch as fetch_mod
from newsfold.ingest.common import FeedEntry
from newsfold.ingest.fetch import run_fetch
from newsfold.models import (
    Article,
    ArticleStatus,
    Source,
    SourceStatus,
    SourceType,
    Topic,
    TopicMode,
    TopicStatus,
)
from newsfold.timeutil import utcnow


@pytest.fixture
def topic(db):
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    return t


def _entries(now: datetime) -> list[FeedEntry]:
    return [
        FeedEntry("Fresh story", "https://n.g/BLOB_A", now - timedelta(hours=2), "The Race", "en"),
        FeedEntry("Old story", "https://n.g/BLOB_B", now - timedelta(days=9), "RACER", "en"),
        FeedEntry("Undated", "https://n.g/BLOB_C", None, "PlanetF1", "en"),
    ]


def test_run_fetch_stores_fresh_skips_stale(db, topic, monkeypatch) -> None:
    now = utcnow()
    monkeypatch.setattr(fetch_mod, "fetch_entries", lambda *a, **k: _entries(now))

    report = run_fetch(db)

    stat = report.stats[0]
    assert (stat.found, stat.added, stat.stale, stat.duplicate) == (3, 2, 1, 0)
    assert db.scalar(select(func.count()).select_from(Article)) == 2
    stored = db.scalars(select(Article)).all()
    assert all(a.status == ArticleStatus.fetched for a in stored)
    assert all(a.run_id == report.run_id for a in stored)


def test_run_fetch_is_idempotent(db, topic, monkeypatch) -> None:
    now = utcnow()
    monkeypatch.setattr(fetch_mod, "fetch_entries", lambda *a, **k: _entries(now))

    run_fetch(db)
    second = run_fetch(db)

    assert second.total_added == 0
    assert second.stats[0].duplicate == 2
    assert db.scalar(select(func.count()).select_from(Article)) == 2


def test_run_fetch_dedupes_by_title_across_different_blob_urls(db, topic, monkeypatch) -> None:
    """Google News re-issues the same real story under a fresh blob URL on every
    fetch, so URL-based dedup alone never catches it — this is the actual bug
    report: `dup=0` forever, the same headlines re-decoded every run."""
    now = utcnow()
    monkeypatch.setattr(
        fetch_mod,
        "fetch_entries",
        lambda *a, **k: [FeedEntry("Verstappen wins again", "https://n.g/BLOB_1", now, "F1", "en")],
    )
    run_fetch(db)

    monkeypatch.setattr(
        fetch_mod,
        "fetch_entries",
        # same story, same title, but a DIFFERENT blob URL — as Google actually does
        lambda *a, **k: [FeedEntry("Verstappen wins again", "https://n.g/BLOB_2", now, "F1", "en")],
    )
    second = run_fetch(db)

    assert second.total_added == 0
    assert second.stats[0].duplicate == 1
    assert db.scalar(select(func.count()).select_from(Article)) == 1


def test_run_fetch_title_dedup_is_case_and_whitespace_insensitive(db, topic, monkeypatch) -> None:
    now = utcnow()
    monkeypatch.setattr(
        fetch_mod,
        "fetch_entries",
        lambda *a, **k: [
            FeedEntry("Verstappen  wins again", "https://n.g/BLOB_1", now, "F1", "en")
        ],
    )
    run_fetch(db)

    monkeypatch.setattr(
        fetch_mod,
        "fetch_entries",
        lambda *a, **k: [FeedEntry("VERSTAPPEN WINS AGAIN", "https://n.g/BLOB_2", now, "F1", "en")],
    )
    second = run_fetch(db)

    assert second.total_added == 0
    assert db.scalar(select(func.count()).select_from(Article)) == 1


def test_run_fetch_title_dedup_ignores_broken_articles(db, topic, monkeypatch) -> None:
    """A broken decode (bad/expired blob) shouldn't permanently block a story
    from being re-fetched — a fresh blob deserves another decode attempt."""
    now = utcnow()
    monkeypatch.setattr(
        fetch_mod,
        "fetch_entries",
        lambda *a, **k: [FeedEntry("Verstappen wins again", "https://n.g/BLOB_1", now, "F1", "en")],
    )
    run_fetch(db)
    db.execute(
        Article.__table__.update()
        .where(Article.source_url == "https://n.g/BLOB_1")
        .values(status=ArticleStatus.broken)
    )
    db.commit()

    monkeypatch.setattr(
        fetch_mod,
        "fetch_entries",
        lambda *a, **k: [FeedEntry("Verstappen wins again", "https://n.g/BLOB_2", now, "F1", "en")],
    )
    second = run_fetch(db)

    assert second.total_added == 1
    assert db.scalar(select(func.count()).select_from(Article)) == 2


def test_run_fetch_survives_feed_error(db, topic, monkeypatch) -> None:
    def boom(*a, **k):
        raise ConnectionError("feed down")

    monkeypatch.setattr(fetch_mod, "fetch_entries", boom)

    report = run_fetch(db)

    assert report.total_added == 0
    assert report.stats[0].error is not None
    assert "feed down" in report.stats[0].error


def test_run_fetch_polls_user_sources(db, topic, monkeypatch) -> None:
    now = utcnow()
    db.add(
        Source(
            input_url="https://the-race.com",
            resolved_feed_url="https://the-race.com/feed",
            name="The Race",
            topic_id=None,  # available to all topics
            status=SourceStatus.active,
        )
    )
    db.flush()

    monkeypatch.setattr(fetch_mod, "fetch_entries", lambda *a, **k: [])
    monkeypatch.setattr(
        fetch_mod,
        "poll_feed",
        lambda *a, **k: [
            FeedEntry("Direct story", "https://the-race.com/s1", now, "The Race", "en")
        ],
    )

    report = run_fetch(db)

    art = db.scalars(select(Article)).one()
    assert art.source_type == SourceType.user_source
    assert art.source_url == "https://the-race.com/s1"
    channels = {s.channel for s in report.stats}
    assert "The Race" in channels

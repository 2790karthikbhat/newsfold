from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

import newsfold.ingest.decode as decode_mod
from newsfold.ingest.decode import DecodeResult, normalize_url, run_decode
from newsfold.models import Article, ArticleStatus, SourceType, Topic, TopicMode, TopicStatus


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


def _blob(db, topic, n: int, *, status=ArticleStatus.fetched, url: str | None = None) -> Article:
    a = Article(
        topic_id=topic.id,
        source_url=url or f"https://news.google.com/rss/articles/BLOB{n}",
        source_name="The Race",
        source_type=SourceType.google_news,
        title_original=f"story {n}",
        language_original="en",
        status=status,
    )
    db.add(a)
    db.flush()
    return a


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://x.com/a?utm_source=news&id=5#frag", "https://x.com/a?id=5"),
        ("https://x.com/a/?fbclid=abc", "https://x.com/a"),
        ("https://x.com/a?b=1&b=2", "https://x.com/a?b=1&b=2"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_run_decode_updates_ok_and_marks_broken(db, topic, monkeypatch) -> None:
    a1, a2 = _blob(db, topic, 1), _blob(db, topic, 2)

    def fake(url: str, *, interval: float) -> DecodeResult:
        if url.endswith("BLOB1"):
            return DecodeResult(ok=True, url="https://the-race.com/story-1")
        return DecodeResult(ok=False, error="ExpiredKey")

    monkeypatch.setattr(decode_mod, "decode_url", fake)

    report = run_decode(db)

    assert (report.considered, report.decoded, report.broken) == (2, 1, 1)
    db.refresh(a1)
    db.refresh(a2)
    assert a1.status == ArticleStatus.decoded
    assert a1.source_url == "https://the-race.com/story-1"
    assert a2.status == ArticleStatus.broken
    assert report.failures == [f"[{a2.id}] ExpiredKey"]


def test_run_decode_collapses_post_decode_duplicates(db, topic, monkeypatch) -> None:
    _blob(db, topic, 1)
    _blob(db, topic, 2)
    monkeypatch.setattr(
        decode_mod,
        "decode_url",
        lambda url, *, interval: DecodeResult(ok=True, url="https://the-race.com/same"),
    )

    report = run_decode(db)

    assert report.decoded == 2
    assert report.deduped == 1
    assert db.scalar(select(func.count()).select_from(Article)) == 1


def test_run_decode_is_idempotent(db, topic, monkeypatch) -> None:
    _blob(db, topic, 1)
    monkeypatch.setattr(
        decode_mod,
        "decode_url",
        lambda url, *, interval: DecodeResult(ok=True, url="https://the-race.com/x"),
    )

    run_decode(db)
    second = run_decode(db)

    assert second.considered == 0


def test_run_decode_only_touches_fetched_google_news(db, topic, monkeypatch) -> None:
    _blob(db, topic, 1, status=ArticleStatus.decoded, url="https://the-race.com/already")
    user = Article(
        topic_id=topic.id,
        source_url="https://blog.example/post",
        source_name="A Blog",
        source_type=SourceType.user_source,
        title_original="user story",
        language_original="en",
        status=ArticleStatus.fetched,
    )
    db.add(user)
    db.flush()

    monkeypatch.setattr(
        decode_mod, "decode_url", lambda url, *, interval: pytest.fail("should not be called")
    )

    report = run_decode(db)
    assert report.considered == 0


def test_run_decode_commits_each_article_so_interrupt_keeps_progress(
    db, topic, monkeypatch
) -> None:
    a1, a2, a3 = _blob(db, topic, 1), _blob(db, topic, 2), _blob(db, topic, 3)
    calls = {"n": 0}

    def fake(url: str, *, interval: float) -> DecodeResult:
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return DecodeResult(ok=True, url=f"https://the-race.com/{calls['n']}")

    monkeypatch.setattr(decode_mod, "decode_url", fake)

    # concurrency=1 pins this to the serial path so "the 3rd call" deterministically
    # means a3 — concurrent mode doesn't guarantee call order.
    with pytest.raises(KeyboardInterrupt):
        run_decode(db, concurrency=1)

    db.rollback()
    db.expire_all()

    assert db.scalar(select(Article.status).where(Article.id == a1.id)) == ArticleStatus.decoded
    assert db.scalar(select(Article.status).where(Article.id == a2.id)) == ArticleStatus.decoded
    assert db.scalar(select(Article.status).where(Article.id == a3.id)) == ArticleStatus.fetched


def test_run_decode_concurrent_matches_serial_outcome(db, topic, monkeypatch) -> None:
    blobs = [_blob(db, topic, i) for i in range(6)]

    def fake(url: str, *, interval: float) -> DecodeResult:
        if url.endswith("BLOB3"):
            return DecodeResult(ok=False, error="nope")
        if url.endswith("BLOB4"):
            raise RuntimeError("boom")  # simulates a worker-thread crash
        return DecodeResult(ok=True, url=f"https://the-race.com/{url[-1]}")

    monkeypatch.setattr(decode_mod, "decode_url", fake)

    report = run_decode(db, concurrency=3)

    assert report.considered == 6
    assert report.decoded == 4
    assert report.broken == 2
    statuses = {b.id: db.scalar(select(Article.status).where(Article.id == b.id)) for b in blobs}
    assert statuses[blobs[3].id] == ArticleStatus.broken  # BLOB3 -> not ok
    assert statuses[blobs[4].id] == ArticleStatus.broken  # BLOB4 -> raised, still handled
    assert statuses[blobs[0].id] == ArticleStatus.decoded

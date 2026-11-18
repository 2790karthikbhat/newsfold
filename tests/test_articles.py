from __future__ import annotations

import json
from datetime import timedelta

import pytest

from newsfold.articles import (
    archive,
    delete,
    mark_read,
    mark_unread,
    run_cleanup,
    save,
    unarchive,
    unsave,
)
from newsfold.models import (
    Article,
    ArticleStatus,
    ReadState,
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


_counter = iter(range(1, 100_000))


def _article(db, topic, *, status=ArticleStatus.clustered, **kw) -> Article:
    n = next(_counter)
    a = Article(
        topic_id=topic.id,
        source_url=f"https://x/{n}",
        source_name="X",
        source_type=SourceType.google_news,
        title_original=kw.pop("title_original", "story"),
        status=status,
        **kw,
    )
    db.add(a)
    db.flush()
    return a


def test_mark_read_and_unread(db, topic) -> None:
    a = _article(db, topic)
    assert mark_read(db, [a.id]) == 1
    assert a.read_state == ReadState.read
    assert mark_unread(db, [a.id]) == 1
    assert a.read_state == ReadState.unread


def test_archive_and_unarchive(db, topic) -> None:
    a = _article(db, topic)
    assert archive(db, [a.id]) == 1
    assert a.read_state == ReadState.archived
    assert unarchive(db, [a.id]) == 1
    assert a.read_state == ReadState.read  # unarchive lands on read, not unread


def test_save_and_unsave_independent_of_read_state(db, topic) -> None:
    a = _article(db, topic)
    archive(db, [a.id])
    assert save(db, [a.id]) == 1
    assert a.saved is True
    assert a.read_state == ReadState.archived  # untouched by save
    assert unsave(db, [a.id]) == 1
    assert a.saved is False


def test_delete_removes_row_regardless_of_saved(db, topic) -> None:
    a = _article(db, topic)
    save(db, [a.id])
    aid = a.id
    assert delete(db, [aid]) == 1
    db.flush()
    from sqlalchemy import select

    assert db.scalar(select(Article).where(Article.id == aid)) is None


def test_mutations_ignore_unknown_ids(db, topic) -> None:
    a = _article(db, topic)
    assert mark_read(db, [a.id, 999999]) == 1  # only the real one counts
    assert mark_read(db, []) == 0


class _RetentionSettings:
    def __init__(self, archive_days=0, delete_days=0):
        self._raw = {
            "auto_archive_after_days": archive_days,
            "auto_delete_after_days": delete_days,
        }

    def section(self, name: str) -> dict:
        return dict(self._raw) if name == "retention" else {}


@pytest.fixture
def old_and_new(db, topic):
    old = utcnow() - timedelta(days=30)
    recent = utcnow() - timedelta(days=1)

    old_unread = _article(db, topic, title_original="old unread", published_at=old, rank_score=1.0)
    recent_unread = _article(
        db, topic, title_original="recent unread", published_at=recent, rank_score=2.0
    )
    old_saved = _article(
        db,
        topic,
        title_original="old saved",
        published_at=old,
        saved=True,
        rank_score=3.0,
    )
    old_discarded = _article(
        db,
        topic,
        title_original="old discarded",
        status=ArticleStatus.discarded,
        published_at=old,
        rank_score=4.0,
    )
    old_broken = _article(
        db, topic, title_original="old broken", status=ArticleStatus.broken, published_at=old
    )
    old_archived_already = _article(
        db,
        topic,
        title_original="already archived",
        published_at=old,
        read_state=ReadState.archived,
    )
    return {
        "old_unread": old_unread,
        "recent_unread": recent_unread,
        "old_saved": old_saved,
        "old_discarded": old_discarded,
        "old_broken": old_broken,
        "old_archived_already": old_archived_already,
    }


def test_run_cleanup_archives_old_unread_only(db, old_and_new, monkeypatch) -> None:
    import newsfold.articles as articles_mod

    monkeypatch.setattr(articles_mod, "load_settings", lambda: _RetentionSettings(archive_days=14))

    report = run_cleanup(db)

    assert report.archive_candidates == 1
    assert report.archived == 1
    assert old_and_new["old_unread"].read_state == ReadState.archived
    assert old_and_new["recent_unread"].read_state == ReadState.unread  # too recent
    assert old_and_new["old_saved"].read_state == ReadState.unread  # saved, never touched


def test_run_cleanup_deletes_old_discarded_and_broken_only(db, old_and_new, monkeypatch) -> None:
    from sqlalchemy import select

    import newsfold.articles as articles_mod

    monkeypatch.setattr(articles_mod, "load_settings", lambda: _RetentionSettings(delete_days=14))

    report = run_cleanup(db)

    assert report.delete_candidates == 2
    assert report.deleted == 2
    remaining_titles = {
        db.scalar(select(Article.title_original).where(Article.id == a.id))
        for a in old_and_new.values()
    } - {None}
    assert "old discarded" not in remaining_titles
    assert "old broken" not in remaining_titles
    assert "old unread" in remaining_titles  # untouched — not discarded/broken
    assert "already archived" in remaining_titles  # archived is never auto-deleted


def test_run_cleanup_dry_run_reports_without_changing(db, old_and_new, monkeypatch) -> None:
    import newsfold.articles as articles_mod

    monkeypatch.setattr(
        articles_mod,
        "load_settings",
        lambda: _RetentionSettings(archive_days=14, delete_days=14),
    )

    report = run_cleanup(db, dry_run=True)

    assert report.archive_candidates == 1
    assert report.archived == 0
    assert report.delete_candidates == 2
    assert report.deleted == 0
    assert old_and_new["old_unread"].read_state == ReadState.unread  # unchanged


def test_run_cleanup_explicit_overrides_beat_config(db, old_and_new, monkeypatch) -> None:
    import newsfold.articles as articles_mod

    monkeypatch.setattr(articles_mod, "load_settings", lambda: _RetentionSettings())

    report = run_cleanup(db, archive_after_days=14, delete_after_days=14)

    assert report.archived == 1
    assert report.deleted == 2

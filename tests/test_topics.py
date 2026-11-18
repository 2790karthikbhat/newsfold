from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from newsfold import topics as topics_mod
from newsfold.config import TopicConfig
from newsfold.models import Topic, TopicMode, TopicStatus
from newsfold.topics import TopicError, add_topic, archive_topic, sync_topics


def _fake_config(monkeypatch, *cfgs: TopicConfig) -> None:
    monkeypatch.setattr(topics_mod, "load_topics", lambda: tuple(cfgs))


def test_sync_adds_new_topics(db, monkeypatch) -> None:
    _fake_config(
        monkeypatch,
        TopicConfig("F1", "temporary", ("en",)),
        TopicConfig("cricket", "temporary", ("en", "vi")),
    )

    report = sync_topics(db)

    assert set(report.added) == {"F1", "cricket"}
    rows = db.scalars(select(Topic)).all()
    assert {r.keyword for r in rows} == {"F1", "cricket"}
    cricket = next(r for r in rows if r.keyword == "cricket")
    assert json.loads(cricket.languages) == ["en", "vi"]


def test_sync_updates_changed_and_leaves_orphans(db, monkeypatch) -> None:
    db.add(
        Topic(
            keyword="F1",
            mode=TopicMode.temporary,
            languages=json.dumps(["en"]),
            status=TopicStatus.active,
        )
    )
    db.add(
        Topic(
            keyword="geography",
            mode=TopicMode.temporary,
            languages=json.dumps(["en"]),
            status=TopicStatus.active,
        )
    )
    db.flush()

    _fake_config(monkeypatch, TopicConfig("f1", "persistent", ("en", "vi")))

    report = sync_topics(db)

    assert report.updated == ["f1"]
    assert report.in_db_not_in_config == ["geography"]
    f1 = db.scalar(select(Topic).where(Topic.keyword == "F1"))
    assert f1.mode == TopicMode.persistent
    assert json.loads(f1.languages) == ["en", "vi"]


def test_add_topic_creates(db) -> None:
    topic = add_topic(db, "cricket", mode="temporary", languages=["en", "vi"])

    assert topic.id is not None
    assert topic.mode == TopicMode.temporary
    assert topic.status == TopicStatus.active
    assert json.loads(topic.languages) == ["en", "vi"]


def test_add_topic_rejects_duplicate_keyword(db) -> None:
    add_topic(db, "F1")
    with pytest.raises(TopicError, match="already exists"):
        add_topic(db, "f1")


def test_add_topic_rejects_invalid_mode(db) -> None:
    with pytest.raises(TopicError, match="mode must be"):
        add_topic(db, "F1", mode="eternal")


def test_add_topic_rejects_empty_keyword(db) -> None:
    with pytest.raises(TopicError, match="empty"):
        add_topic(db, "  ")


def test_add_topic_rejects_empty_languages(db) -> None:
    with pytest.raises(TopicError, match="languages"):
        add_topic(db, "F1", languages=[])


def test_archive_topic_marks_archived(db) -> None:
    topic = add_topic(db, "F1")

    assert archive_topic(db, topic.id) is True
    assert topic.status == TopicStatus.archived


def test_archive_topic_unknown_id_returns_false(db) -> None:
    assert archive_topic(db, 999) is False

from __future__ import annotations

import json

from sqlalchemy import select

from newsfold.models import Topic, TopicMode, TopicStatus


def test_list_topics_empty(client) -> None:
    resp = client.get("/topics")
    assert resp.status_code == 200
    assert "No topics yet" in resp.text


def test_create_topic(client, db) -> None:
    resp = client.post(
        "/topics",
        data={"keyword": "cricket", "mode": "temporary", "languages": "en, vi"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "msg=" in resp.headers["location"]

    db.expire_all()
    row = db.scalar(select(Topic).where(Topic.keyword == "cricket"))
    assert row is not None
    assert row.mode == TopicMode.temporary
    assert json.loads(row.languages) == ["en", "vi"]


def test_create_duplicate_topic_redirects_with_error(client, db) -> None:
    # Committed, not just flushed: the route runs its own Session on the same
    # StaticPool connection and would otherwise contend with this open transaction.
    db.add(
        Topic(
            keyword="F1",
            mode=TopicMode.temporary,
            languages=json.dumps(["en"]),
            status=TopicStatus.active,
        )
    )
    db.commit()

    resp = client.post(
        "/topics",
        data={"keyword": "F1", "mode": "temporary", "languages": "en"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    db.expire_all()
    assert len(db.scalars(select(Topic).where(Topic.keyword == "F1")).all()) == 1


def test_archive_topic(client, db) -> None:
    topic = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(topic)
    db.commit()
    topic_id = topic.id

    resp = client.post(f"/topics/{topic_id}/archive", follow_redirects=False)

    assert resp.status_code == 303
    assert "msg=archived" in resp.headers["location"]
    db.expire_all()
    row = db.get(Topic, topic_id)
    assert row.status == TopicStatus.archived


def test_archive_unknown_topic_redirects_with_error(client) -> None:
    resp = client.post("/topics/999/archive", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

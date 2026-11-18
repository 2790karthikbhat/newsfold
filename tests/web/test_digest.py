from __future__ import annotations

import json

from newsfold.models import Article, ArticleStatus, SourceType, Topic, TopicMode, TopicStatus
from newsfold.web.routes import digest as digest_routes


def _make_topic_with_article(db) -> int:
    # Committed (not just flushed): the route reads through a separate Session on
    # the same StaticPool connection, so this transaction must be closed first.
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
    )
    db.add(article)
    db.commit()
    return article.id


def test_home_page(client, db) -> None:
    _make_topic_with_article(db)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "unread" in resp.text
    assert "Verstappen wins again" in resp.text


def test_digest_index_lists_topics(client, db) -> None:
    _make_topic_with_article(db)
    resp = client.get("/digest")
    assert resp.status_code == 200
    assert "F1" in resp.text
    assert "Verstappen wins again" in resp.text


def test_digest_index_empty(client) -> None:
    resp = client.get("/digest")
    assert resp.status_code == 200
    assert "No live stories yet" in resp.text


def test_digest_topic_view(client, db) -> None:
    _make_topic_with_article(db)
    resp = client.get("/digest/topic/F1")
    assert resp.status_code == 200
    assert "Verstappen wins again" in resp.text


def test_digest_topic_view_case_insensitive(client, db) -> None:
    _make_topic_with_article(db)
    resp = client.get("/digest/topic/f1")
    assert resp.status_code == 200


def test_digest_topic_view_unknown_returns_404(client) -> None:
    resp = client.get("/digest/topic/nonexistent")
    assert resp.status_code == 404


def test_digest_article_view(client, db) -> None:
    article_id = _make_topic_with_article(db)
    resp = client.get(f"/digest/article/{article_id}")
    assert resp.status_code == 200
    assert "Verstappen wins again" in resp.text


def test_digest_article_view_unknown_returns_404(client) -> None:
    resp = client.get("/digest/article/999999")
    assert resp.status_code == 404


def test_digest_latest_redirects_to_index(client) -> None:
    resp = client.get("/digest/latest", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/digest"


def test_digest_archive_lists_files(client, monkeypatch) -> None:
    from pathlib import Path

    from newsfold.web.digest_history import DigestFile

    monkeypatch.setattr(
        digest_routes,
        "list_digest_files",
        lambda: [
            DigestFile(run_date="2026-01-01", format="html", path=Path("digest-2026-01-01.html"))
        ],
    )
    resp = client.get("/digest/archive")
    assert resp.status_code == 200
    assert "2026-01-01" in resp.text


def test_digest_archive_file_serves_saved_content(client, monkeypatch) -> None:
    monkeypatch.setattr(
        digest_routes, "read_digest_file", lambda run_date, fmt="html": "<p>hello</p>"
    )
    resp = client.get("/digest/archive/2026-01-01")
    assert resp.status_code == 200
    assert "<p>hello</p>" in resp.text


def test_digest_archive_file_missing_returns_404(client) -> None:
    resp = client.get("/digest/archive/2000-01-01")
    assert resp.status_code == 404


def test_article_read_route_marks_read_and_redirects(client, db) -> None:
    article_id = _make_topic_with_article(db)

    resp = client.post(
        f"/digest/article/{article_id}/read",
        data={"next": f"/digest/article/{article_id}"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/digest/article/")
    followed = client.get(resp.headers["location"])
    assert "marked read" in followed.text
    assert "read" in followed.text  # the badge


def test_article_read_route_without_next_falls_back_to_digest_index(client, db) -> None:
    article_id = _make_topic_with_article(db)

    resp = client.post(f"/digest/article/{article_id}/read", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/digest?msg=marked+read"


def test_article_archive_removes_it_from_topic_view_but_not_detail(client, db) -> None:
    article_id = _make_topic_with_article(db)

    client.post(f"/digest/article/{article_id}/archive")

    topic_resp = client.get("/digest/topic/F1")
    assert "Verstappen wins again" not in topic_resp.text

    detail_resp = client.get(f"/digest/article/{article_id}")
    assert detail_resp.status_code == 200
    assert "Verstappen wins again" in detail_resp.text
    assert "archived" in detail_resp.text


def test_article_save_shows_up_on_saved_page_unsave_removes_it(client, db) -> None:
    article_id = _make_topic_with_article(db)

    client.post(f"/digest/article/{article_id}/save")
    saved_resp = client.get("/digest/saved")
    assert "Verstappen wins again" in saved_resp.text

    client.post(f"/digest/article/{article_id}/unsave")
    saved_resp = client.get("/digest/saved")
    assert "Verstappen wins again" not in saved_resp.text
    assert "Nothing saved yet" in saved_resp.text


def test_article_delete_removes_it_permanently(client, db) -> None:
    article_id = _make_topic_with_article(db)

    resp = client.post(f"/digest/article/{article_id}/delete", follow_redirects=False)

    assert resp.status_code == 303
    assert client.get(f"/digest/article/{article_id}").status_code == 404


def test_article_action_on_unknown_id_redirects_with_error(client) -> None:
    resp = client.post("/digest/article/999999/read", follow_redirects=False)

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_article_action_next_param_rejects_external_urls(client, db) -> None:
    article_id = _make_topic_with_article(db)

    resp = client.post(
        f"/digest/article/{article_id}/read",
        data={"next": "https://evil.example/steal"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/digest")
    assert "evil.example" not in location


def test_saved_page_empty_state(client) -> None:
    resp = client.get("/digest/saved")
    assert resp.status_code == 200
    assert "Nothing saved yet" in resp.text

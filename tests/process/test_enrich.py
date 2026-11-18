from __future__ import annotations

import json

import pytest
from sqlalchemy import select

import newsfold.process.enrich as enrich_mod
from newsfold.llm.base import LLMError
from newsfold.models import Article, ArticleStatus, SourceType, Topic, TopicMode, TopicStatus
from newsfold.process.enrich import _parse, run_enrich


def test_parse_normalizes_fields() -> None:
    r = _parse(
        {
            "is_relevant": True,
            "reason": " on topic ",
            "language": "VI",
            "title_translated": "Hello",
            "summary": "A summary.",
            "body_translated": "null",
        }
    )
    assert r.is_relevant and r.reason == "on topic"
    assert r.language == "vi"
    assert r.body_translated is None


@pytest.fixture
def clustered(db):
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    keep = Article(
        topic_id=t.id,
        source_url="https://x/keep",
        source_name="X",
        source_type=SourceType.google_news,
        title_original="Verstappen wins",
        body_original="Max Verstappen won the race.",
        status=ArticleStatus.clustered,
        rank_score=5.0,
        llm_rank=1,
    )
    drop = Article(
        topic_id=t.id,
        source_url="https://x/drop",
        source_name="X",
        source_type=SourceType.google_news,
        title_original="Unrelated recipe",
        status=ArticleStatus.clustered,
        rank_score=2.0,
    )
    db.add_all([keep, drop])
    db.flush()
    return db, keep, drop


def _fake_complete(mapping: dict[str, dict]):
    def inner(*, system, user, task="enrich", dry_run=False, **kw):
        if dry_run:
            return {}
        for needle, payload in mapping.items():
            if needle in user:
                return payload
        raise AssertionError(f"no fake reply matches: {user!r}")

    return inner


def test_run_enrich_processes_and_discards(clustered, monkeypatch) -> None:
    db, keep, drop = clustered
    monkeypatch.setattr(
        enrich_mod,
        "complete_json",
        _fake_complete(
            {
                "Verstappen wins": {
                    "is_relevant": True,
                    "reason": "race result",
                    "language": "en",
                    "title_translated": "Verstappen wins",
                    "summary": "He won.",
                    "body_translated": "Max Verstappen won the race.",
                },
                "Unrelated recipe": {"is_relevant": False, "reason": "cooking, not F1"},
            }
        ),
    )
    monkeypatch.setattr(enrich_mod, "describe", lambda task="enrich": "fake:model")

    report = run_enrich(db)

    assert (report.enriched, report.discarded, report.failed) == (1, 1, 0)
    assert keep.status == ArticleStatus.processed
    assert keep.summary == "He won."
    assert drop.status == ArticleStatus.discarded
    assert drop.llm_reason == "cooking, not F1"


def test_run_enrich_is_idempotent(clustered, monkeypatch) -> None:
    db, keep, drop = clustered
    monkeypatch.setattr(
        enrich_mod,
        "complete_json",
        _fake_complete(
            {
                "Verstappen wins": {
                    "is_relevant": True,
                    "reason": "r",
                    "language": "en",
                    "title_translated": "t",
                    "summary": "s",
                    "body_translated": None,
                },
                "Unrelated recipe": {"is_relevant": False, "reason": "x"},
            }
        ),
    )
    monkeypatch.setattr(enrich_mod, "describe", lambda task="enrich": "fake")

    run_enrich(db)
    second = run_enrich(db)
    assert second.considered == 0


def test_run_enrich_failure_keeps_row_clustered(clustered, monkeypatch) -> None:
    db, keep, drop = clustered

    def boom(**kw):
        raise LLMError("provider down")

    monkeypatch.setattr(enrich_mod, "complete_json", boom)
    monkeypatch.setattr(enrich_mod, "describe", lambda task="enrich": "fake")

    report = run_enrich(db)

    assert report.failed == 2
    assert keep.status == ArticleStatus.clustered
    assert "provider down" in report.failures[0]


def test_run_enrich_dry_run_counts_only(clustered, monkeypatch) -> None:
    db, keep, drop = clustered
    monkeypatch.setattr(enrich_mod, "complete_json", lambda **kw: {})
    monkeypatch.setattr(enrich_mod, "describe", lambda task="enrich": "fake")

    report = run_enrich(db, dry_run=True)

    assert report.planned == 2
    assert db.scalar(select(Article.status).where(Article.id == keep.id)) == ArticleStatus.clustered


def test_run_enrich_commits_each_article_so_interrupt_keeps_progress(db, monkeypatch) -> None:
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    arts = [
        Article(
            topic_id=t.id,
            source_url=f"https://x/{i}",
            source_name="X",
            source_type=SourceType.google_news,
            title_original=f"story {i}",
            status=ArticleStatus.clustered,
            rank_score=float(3 - i),
        )
        for i in range(3)
    ]
    db.add_all(arts)
    db.flush()

    calls = {"n": 0}

    def fake(*, system, user, task="enrich", dry_run=False, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return {
            "is_relevant": True,
            "reason": "r",
            "language": "en",
            "title_translated": "t",
            "summary": "s",
            "body_translated": None,
        }

    monkeypatch.setattr(enrich_mod, "complete_json", fake)
    monkeypatch.setattr(enrich_mod, "describe", lambda task="enrich": "fake")

    with pytest.raises(KeyboardInterrupt):
        run_enrich(db)

    # simulate what session_scope() does on an uncaught interrupt: no final commit,
    # just an implicit rollback of whatever transaction is still open
    db.rollback()
    db.expire_all()

    assert db.scalar(select(Article.status).where(Article.id == arts[0].id)) == (
        ArticleStatus.processed
    )
    assert db.scalar(select(Article.status).where(Article.id == arts[1].id)) == (
        ArticleStatus.processed
    )
    assert db.scalar(select(Article.status).where(Article.id == arts[2].id)) == (
        ArticleStatus.clustered
    )

from __future__ import annotations

import json

import pytest

import newsfold.rank.llm_rank as rank_mod
from newsfold.llm.base import LLMError
from newsfold.models import Article, ArticleStatus, SourceType, Topic, TopicMode, TopicStatus
from newsfold.rank.llm_rank import _parse_ranked, run_llm_rank


def test_parse_ranked_filters_bad_entries() -> None:
    out = _parse_ranked(
        {
            "ranked": [
                {"index": 2, "reason": "big"},
                {"index": 2, "reason": "dupe"},  # dropped
                {"index": 99, "reason": "oob"},  # dropped
                {"reason": "no index"},  # dropped
                {"index": 1, "reason": "ok"},
            ]
        },
        n=3,
    )
    assert out == [(2, "big"), (1, "ok")]


@pytest.fixture
def topic_with_candidates(db):
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    arts = []
    for i, score in enumerate([9.0, 8.0, 7.0], start=1):
        a = Article(
            topic_id=t.id,
            source_url=f"https://x/{i}",
            source_name="X",
            source_type=SourceType.google_news,
            title_original=f"Story {i}",
            status=ArticleStatus.clustered,
            rank_score=score,
            cluster_id=f"c{i}",
        )
        db.add(a)
        arts.append(a)
    db.flush()
    return db, arts


def test_run_llm_rank_promotes_and_discards(topic_with_candidates, monkeypatch) -> None:
    db, arts = topic_with_candidates
    # candidates are ordered by rank_score desc: [Story 1, Story 2, Story 3]
    monkeypatch.setattr(
        rank_mod,
        "complete_json",
        lambda **kw: {
            "ranked": [{"index": 3, "reason": "sleeper"}, {"index": 1, "reason": "lead"}]
        },
    )
    monkeypatch.setattr(rank_mod, "describe", lambda task="rank": "fake:model")
    monkeypatch.setattr(
        rank_mod,
        "load_settings",
        lambda: _Settings(keep=2),
    )

    report = run_llm_rank(db)

    stat = report.stats[0]
    assert (stat.promoted, stat.discarded) == (2, 1)
    assert arts[2].llm_rank == 1 and arts[2].llm_reason == "sleeper"  # index 3 → position 1
    assert arts[0].llm_rank == 2  # index 1 → position 2
    assert arts[1].status == ArticleStatus.discarded  # index 2 not chosen


def test_run_llm_rank_skips_single_candidate(db, monkeypatch) -> None:
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    db.add(
        Article(
            topic_id=t.id,
            source_url="https://x/1",
            source_name="X",
            source_type=SourceType.google_news,
            title_original="only one",
            status=ArticleStatus.clustered,
            rank_score=5.0,
        )
    )
    db.flush()
    monkeypatch.setattr(rank_mod, "complete_json", lambda **kw: pytest.fail("should not call LLM"))
    monkeypatch.setattr(rank_mod, "describe", lambda task="rank": "fake")
    monkeypatch.setattr(rank_mod, "load_settings", lambda: _Settings())

    report = run_llm_rank(db)
    assert report.stats[0].skipped is True


def test_run_llm_rank_records_llm_error(topic_with_candidates, monkeypatch) -> None:
    db, arts = topic_with_candidates

    def boom(**kw):
        raise LLMError("timeout")

    monkeypatch.setattr(rank_mod, "complete_json", boom)
    monkeypatch.setattr(rank_mod, "describe", lambda task="rank": "fake")
    monkeypatch.setattr(rank_mod, "load_settings", lambda: _Settings())

    report = run_llm_rank(db)
    assert report.stats[0].error == "timeout"
    assert all(a.status == ArticleStatus.clustered for a in arts)  # unchanged


class _Settings:
    def __init__(self, *, keep: int = 8) -> None:
        self._keep = keep

    def section(self, name: str) -> dict:
        return {"candidate_clusters_per_topic": 18, "keep_per_topic": self._keep}

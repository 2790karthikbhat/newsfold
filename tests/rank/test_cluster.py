from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from newsfold.models import (
    Article,
    ArticleStatus,
    SourceType,
    Topic,
    TopicMode,
    TopicStatus,
)
from newsfold.rank.cluster import cluster_articles, jaccard, run_cluster, tokenize
from newsfold.timeutil import utcnow


def test_tokenize_strips_diacritics_stopwords_and_short_tokens() -> None:
    toks = tokenize("Đội tuyển Việt Nam thắng in the final")
    assert "doi" in toks and "tuyen" in toks and "viet" in toks
    assert "in" not in toks and "the" not in toks  # stopwords
    assert all(len(t) >= 2 for t in toks)


def test_jaccard() -> None:
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b", "c", "d"}, {"a", "b"}) == 0.5
    assert jaccard(set(), {"a"}) == 0.0


_counter = iter(range(1, 10_000))


def _art(
    title: str, *, hours_ago: float = 1, lang: str = "en", domain: str | None = None
) -> Article:
    n = next(_counter)
    host = domain or f"pub{n}.example"
    return Article(
        source_url=f"https://{host}/{n}",
        source_name=host,
        source_type=SourceType.google_news,
        title_original=title,
        language_original=lang,
        published_at=utcnow() - timedelta(hours=hours_ago),
        status=ArticleStatus.decoded,
    )


def test_cluster_articles_groups_similar_titles() -> None:
    arts = [
        _art("Alpine confirms Colapinto for the 2027 season"),
        _art("Colapinto confirmed at Alpine for 2027"),
        _art("Red Bull unveils new engine details"),
    ]
    groups = cluster_articles(arts, threshold=0.3, window=timedelta(days=1))
    assert sorted(len(g) for g in groups) == [1, 2]


def test_cluster_articles_splits_when_outside_time_window() -> None:
    arts = [
        _art("Alpine confirms Colapinto for 2027", hours_ago=1),
        _art("Alpine confirms Colapinto for 2027", hours_ago=72),
    ]
    groups = cluster_articles(arts, threshold=0.3, window=timedelta(days=1))
    assert len(groups) == 2


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


def test_run_cluster_assigns_ids_scores_and_survivors(db, topic, monkeypatch) -> None:
    import newsfold.rank.cluster as cluster_mod

    monkeypatch.setattr(
        cluster_mod,
        "load_settings",
        lambda: _settings(candidate_clusters_per_topic=1),
    )

    a = _art("Alpine confirms Colapinto for 2027")
    b = _art("Colapinto confirmed at Alpine for 2027")
    c = _art("Totally unrelated Red Bull engine story")
    for art in (a, b, c):
        art.topic_id = topic.id
        db.add(art)
    db.flush()

    report = run_cluster(db)
    db.flush()

    assert a.cluster_id is not None
    assert a.cluster_id == b.cluster_id != c.cluster_id
    # representative of the 2-article cluster carries the score
    reps = [x for x in (a, b) if x.rank_score is not None]
    assert len(reps) == 1
    assert reps[0].status == ArticleStatus.clustered  # top-1 survivor
    assert c.status == ArticleStatus.decoded  # not a survivor
    assert report.total_survivors == 1


def test_run_cluster_is_idempotent(db, topic, monkeypatch) -> None:
    import newsfold.rank.cluster as cluster_mod

    monkeypatch.setattr(cluster_mod, "load_settings", lambda: _settings())
    art = _art("Solo story about something")
    art.topic_id = topic.id
    db.add(art)
    db.flush()

    run_cluster(db)
    first_cid = db.scalar(select(Article.cluster_id))
    report2 = run_cluster(db)

    assert db.scalar(select(Article.cluster_id)) == first_cid
    assert report2.total_clusters == 0


class _settings:
    """Minimal stand-in for config.Settings used by run_cluster."""

    def __init__(self, **ranking_overrides):
        self._ranking = {
            "candidate_clusters_per_topic": 18,
            "tier1_bonus": 2.0,
            **ranking_overrides,
        }

    def section(self, name: str) -> dict:
        if name == "clustering":
            return {
                "same_story_title_jaccard": 0.3,
                "publish_window_days": 1,
                "recency_half_life_hours": 24,
            }
        return self._ranking

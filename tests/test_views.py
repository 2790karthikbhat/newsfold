from __future__ import annotations

import json

import pytest

from newsfold.models import (
    Article,
    ArticleStatus,
    ReadState,
    SourceType,
    Topic,
    TopicMode,
    TopicStatus,
)
from newsfold.views import list_articles, pipeline_status


@pytest.fixture
def seeded(db):
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    db.add_all(
        [
            Article(
                topic_id=t.id,
                source_url="https://the-race.com/a",
                source_name="The Race",
                source_type=SourceType.google_news,
                title_original="low",
                status=ArticleStatus.decoded,
                rank_score=1.0,
                cluster_id="c1",
            ),
            Article(
                topic_id=t.id,
                source_url="https://formula1.com/b",
                source_name="F1",
                source_type=SourceType.user_source,
                title_original="high",
                status=ArticleStatus.clustered,
                rank_score=9.0,
                cluster_id="c2",
                read_state=ReadState.read,
                saved=True,
            ),
        ]
    )
    db.flush()
    return t


def test_list_articles_orders_by_score_desc(seeded, db) -> None:
    rows = list_articles(db)
    assert [r.title for r in rows] == ["high", "low"]


def test_list_articles_filters(seeded, db) -> None:
    assert len(list_articles(db, status="decoded")) == 1
    assert len(list_articles(db, cluster_id="c2")) == 1
    assert list_articles(db, status="decoded")[0].source_url == "https://the-race.com/a"


def test_list_articles_filters_read_state_and_saved(seeded, db) -> None:
    unread = list_articles(db, read_state="unread")
    assert [r.title for r in unread] == ["low"]

    saved = list_articles(db, saved=True)
    assert [r.title for r in saved] == ["high"]
    assert saved[0].read_state == "read"


def test_pipeline_status_counts(seeded, db) -> None:
    st = pipeline_status(db)
    assert st.total_articles == 2
    assert st.total_clusters == 2
    assert st.articles_by_status == {"decoded": 1, "clustered": 1}
    assert st.articles_by_read_state == {"unread": 1, "read": 1}
    assert st.articles_saved == 1
    assert st.articles_by_source_type == {"google_news": 1, "user_source": 1}

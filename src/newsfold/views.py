"""Read-only DB inspection helpers for the CLI (`digest articles`, `digest status`)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from newsfold.models import Article, ArticleStatus, ReadState, Run, Topic


@dataclass
class ArticleRow:
    id: int
    status: str
    read_state: str
    saved: bool
    source_type: str
    topic: str
    cluster_id: str | None
    rank_score: float | None
    source_name: str
    source_url: str
    title: str


def list_articles(
    session: Session,
    *,
    status: str | None = None,
    read_state: str | None = None,
    saved: bool | None = None,
    topic_keyword: str | None = None,
    cluster_id: str | None = None,
    limit: int = 50,
) -> list[ArticleRow]:
    stmt = (
        select(Article, Topic.keyword)
        .join(Topic)
        .order_by(Article.rank_score.desc().nullslast(), Article.id)
    )
    if status:
        stmt = stmt.where(Article.status == ArticleStatus(status))
    if read_state:
        stmt = stmt.where(Article.read_state == ReadState(read_state))
    if saved is not None:
        stmt = stmt.where(Article.saved.is_(saved))
    if topic_keyword:
        stmt = stmt.where(func.lower(Topic.keyword) == topic_keyword.lower())
    if cluster_id:
        stmt = stmt.where(Article.cluster_id == cluster_id)
    stmt = stmt.limit(limit)

    rows: list[ArticleRow] = []
    for article, keyword in session.execute(stmt):
        rows.append(
            ArticleRow(
                id=article.id,
                status=article.status.value,
                read_state=article.read_state.value,
                saved=article.saved,
                source_type=article.source_type.value,
                topic=keyword,
                cluster_id=article.cluster_id,
                rank_score=article.rank_score,
                source_name=article.source_name,
                source_url=article.source_url,
                title=article.title_translated or article.title_original,
            )
        )
    return rows


@dataclass
class PipelineStatus:
    articles_by_status: dict[str, int]
    articles_by_read_state: dict[str, int]
    articles_saved: int
    articles_by_source_type: dict[str, int]
    total_articles: int
    total_clusters: int
    latest_run: dict[str, object] | None


def pipeline_status(session: Session) -> PipelineStatus:
    by_status = {
        s.value: session.scalar(
            select(func.count()).select_from(Article).where(Article.status == s)
        )
        or 0
        for s in ArticleStatus
    }
    by_read_state = {
        s.value: session.scalar(
            select(func.count()).select_from(Article).where(Article.read_state == s)
        )
        or 0
        for s in ReadState
    }
    saved_count = (
        session.scalar(select(func.count()).select_from(Article).where(Article.saved.is_(True)))
        or 0
    )
    by_type: dict[str, int] = {
        src_type.value: count
        for src_type, count in session.execute(
            select(Article.source_type, func.count()).group_by(Article.source_type)
        ).all()
    }
    total = session.scalar(select(func.count()).select_from(Article)) or 0
    clusters = (
        session.scalar(
            select(func.count(func.distinct(Article.cluster_id))).where(
                Article.cluster_id.is_not(None)
            )
        )
        or 0
    )
    run = session.scalars(select(Run).order_by(Run.id.desc()).limit(1)).first()
    latest = None
    if run is not None:
        latest = {
            "id": run.id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "stats": json.loads(run.stats) if run.stats else None,
        }
    return PipelineStatus(
        articles_by_status={k: v for k, v in by_status.items() if v},
        articles_by_read_state={k: v for k, v in by_read_state.items() if v},
        articles_saved=saved_count,
        articles_by_source_type=by_type,
        total_articles=total,
        total_clusters=clusters,
        latest_run=latest,
    )

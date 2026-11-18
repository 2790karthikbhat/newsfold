"""SQLAlchemy ORM models.

SQLite-backed; column types chosen to port to Postgres unchanged.
Migrations are forward-only (Alembic) — see CLAUDE.md.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TopicMode(str, enum.Enum):
    persistent = "persistent"
    temporary = "temporary"


class TopicStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class SourceStatus(str, enum.Enum):
    active = "active"
    broken = "broken"


class SourceType(str, enum.Enum):
    google_news = "google_news"
    user_source = "user_source"


class ArticleStatus(str, enum.Enum):
    fetched = "fetched"  # raw from feed
    decoded = "decoded"  # real URL resolved (google_news only)
    clustered = "clustered"  # cluster representative that survived cluster scoring
    processed = "processed"  # translated + summarized (enrich)
    discarded = "discarded"  # dropped by LLM rank or classify — kept for audit
    broken = "broken"  # decode failed — unusable, skipped downstream


class ReadState(str, enum.Enum):
    """Reader-driven state, orthogonal to `ArticleStatus` (the pipeline's own
    progress ladder). Only meaningful for articles a human could actually have
    seen (`status in (clustered, processed)`) — everything else stays `unread` by
    construction since it was never shown. `saved` (see Article.saved) is a
    separate, independent flag — "read later" isn't a point on this progression."""

    unread = "unread"
    read = "read"
    archived = "archived"  # explicitly dismissed; reversible — the row is untouched


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(Text)
    mode: Mapped[TopicMode] = mapped_column(Enum(TopicMode))
    languages: Mapped[str] = mapped_column(Text)  # JSON-encoded list, e.g. '["vi","en"]'
    status: Mapped[TopicStatus] = mapped_column(Enum(TopicStatus), default=TopicStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    articles: Mapped[list[Article]] = relationship(back_populates="topic")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    input_url: Mapped[str] = mapped_column(Text)
    resolved_feed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    status: Mapped[SourceStatus] = mapped_column(Enum(SourceStatus), default=SourceStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Run(Base):
    """One pipeline invocation — scopes 'this digest' and keeps runs idempotent."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stats: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON summary line


class LLMUsage(Base):
    """Token totals per run x stage x provider x model. `run_id` is NULL for a
    standalone `digest process` / `digest rank` outside `digest run`."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    stage: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(200))
    calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    calls_without_usage: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)

    source_url: Mapped[str] = mapped_column(Text)  # dedupe key (real URL once decoded)
    source_name: Mapped[str] = mapped_column(Text)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    title_original: Mapped[str] = mapped_column(Text)
    title_translated: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_translated: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_original: Mapped[str | None] = mapped_column(String(8), nullable=True)

    cluster_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rank_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ArticleStatus] = mapped_column(
        Enum(ArticleStatus), default=ArticleStatus.fetched
    )
    headline_only: Mapped[bool] = mapped_column(default=False)  # full-text fetch failed
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    read_state: Mapped[ReadState] = mapped_column(Enum(ReadState), default=ReadState.unread)
    saved: Mapped[bool] = mapped_column(default=False)  # "read later" — independent of read_state

    topic: Mapped[Topic] = relationship(back_populates="articles")

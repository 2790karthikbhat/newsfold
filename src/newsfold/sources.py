"""Manage the `sources` table: add a website URL, resolve its feed, store it.

The DB is the source of truth at runtime; `config/sources.yaml` is only a seed list
consumed by `sync_sources`.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from newsfold.config import load_sources
from newsfold.ingest.user_sources import DiscoveryResult, discover_feed
from newsfold.models import Source, SourceStatus, Topic


class SourceError(RuntimeError):
    """Raised when a source cannot be added (e.g. unknown topic)."""


@dataclass
class AddResult:
    source: Source
    discovery: DiscoveryResult
    created: bool


def _resolve_topic_id(session: Session, topic_keyword: str | None) -> int | None:
    if not topic_keyword:
        return None
    topic = session.scalar(select(Topic).where(func.lower(Topic.keyword) == topic_keyword.lower()))
    if topic is None:
        raise SourceError(
            f"unknown topic {topic_keyword!r} — add it to config/topics.yaml and run "
            f"`digest topics sync` first"
        )
    return topic.id


def add_source(
    session: Session,
    input_url: str,
    *,
    name: str | None = None,
    topic_keyword: str | None = None,
    timeout: float = 20.0,
) -> AddResult:
    input_url = input_url.strip()
    topic_id = _resolve_topic_id(session, topic_keyword)

    existing = session.scalar(select(Source).where(Source.input_url == input_url))
    discovery = discover_feed(input_url, timeout=timeout)

    row = existing or Source(input_url=input_url)
    row.topic_id = topic_id
    if discovery.ok and discovery.feed_url:
        row.resolved_feed_url = discovery.feed_url
        row.name = name or discovery.feed_title or urlparse(discovery.feed_url).netloc
        row.status = SourceStatus.active
    else:
        row.name = name or row.name or urlparse(input_url).netloc or input_url
        row.status = SourceStatus.broken

    if existing is None:
        session.add(row)
    return AddResult(source=row, discovery=discovery, created=existing is None)


def list_sources(session: Session) -> list[Source]:
    return list(session.scalars(select(Source).order_by(Source.id)).all())


def remove_source(session: Session, source_id: int) -> bool:
    row = session.get(Source, source_id)
    if row is None:
        return False
    session.delete(row)
    return True


@dataclass
class SyncReport:
    results: list[AddResult]


def sync_sources(session: Session, *, timeout: float = 20.0) -> SyncReport:
    results = [
        add_source(
            session,
            cfg.input_url,
            name=cfg.name,
            topic_keyword=cfg.topic,
            timeout=timeout,
        )
        for cfg in load_sources()
    ]
    return SyncReport(results=results)

"""Ingestion orchestrator: pull Google News RSS + active user feeds into Article rows.

No LLM, no decoding, no clustering. Idempotent: an entry already stored for a topic
(same `source_url`, or same normalized title — see below) is skipped, as are entries
older than `ingestion.lookback_days`. User-source feeds already carry real URLs (they
skip decoding); Google News links are still blobs at this stage.

Google News's blob-encoded URLs are not stable across separate fetches — the same
real story gets re-issued under a *different* blob each time, so URL-based dedup
alone never catches it (confirmed live: google_news channels showed `dup=0` on every
fetch, even fetch after fetch of the same top headlines). Every "new" blob for a
story already decoded would otherwise get re-decoded (a real Google-decoder round
trip) only to be pruned afterward by `decode._dedupe_topic` once the real URL is
known — correct end state, wasted work getting there. Titles are available
immediately at fetch time and don't have this instability, so `known_titles`
(normalized, topic-scoped, excluding `broken` rows — a broken decode deserves a
retry under a fresh blob, not a permanent skip) catches these before they're ever
stored, decoded, or rate-limited against Google's endpoint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from newsfold.config import load_settings
from newsfold.ingest.common import FeedEntry
from newsfold.ingest.google_news import fetch_entries
from newsfold.ingest.user_sources import poll_feed
from newsfold.models import (
    Article,
    ArticleStatus,
    Run,
    Source,
    SourceStatus,
    SourceType,
    Topic,
    TopicStatus,
)
from newsfold.progress import ProgressCallback, noop
from newsfold.timeutil import utcnow


@dataclass
class ChannelStat:
    keyword: str
    channel: str  # a query language ("vi") or a user source name ("Tuổi Trẻ")
    found: int = 0
    added: int = 0
    stale: int = 0
    duplicate: int = 0
    error: str | None = None


@dataclass
class FetchReport:
    run_id: int
    stats: list[ChannelStat] = field(default_factory=list)

    @property
    def total_added(self) -> int:
        return sum(s.added for s in self.stats)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_added": self.total_added,
            "channels": [vars(s) for s in self.stats],
        }


def _normalize_title(title: str) -> str:
    return " ".join(title.split()).lower()


def _region_for(language: str, ingestion_cfg: dict[str, Any]) -> str:
    mapping = ingestion_cfg.get("region_by_language") or {}
    return str(mapping.get(language) or ingestion_cfg.get("default_region") or "US")


def _sources_for_topic(session: Session, topic_id: int) -> list[Source]:
    return list(
        session.scalars(
            select(Source)
            .where(
                Source.status == SourceStatus.active,
                Source.resolved_feed_url.is_not(None),
                or_(Source.topic_id == topic_id, Source.topic_id.is_(None)),
            )
            .order_by(Source.id)
        ).all()
    )


def _store_entries(
    session: Session,
    entries: list[FeedEntry],
    *,
    topic: Topic,
    run: Run,
    source_type: SourceType,
    known_urls: set[str],
    known_titles: set[str],
    cutoff: datetime,
    stat: ChannelStat,
) -> None:
    stat.found = len(entries)
    for entry in entries:
        if entry.published_at and entry.published_at < cutoff:
            stat.stale += 1
            continue
        normalized_title = _normalize_title(entry.title)
        if entry.link in known_urls or normalized_title in known_titles:
            stat.duplicate += 1
            continue
        known_urls.add(entry.link)
        known_titles.add(normalized_title)
        session.add(
            Article(
                topic_id=topic.id,
                run_id=run.id,
                source_url=entry.link,
                source_name=entry.source_name,
                source_type=source_type,
                published_at=entry.published_at,
                title_original=entry.title,
                language_original=entry.language,
                status=ArticleStatus.fetched,
            )
        )
        stat.added += 1


def run_fetch(session: Session, *, progress: ProgressCallback = noop) -> FetchReport:
    ing = load_settings().section("ingestion")
    lookback_days = int(ing.get("lookback_days", 2))
    timeout = float(ing.get("http_timeout_sec", 20))
    cutoff = utcnow() - timedelta(days=lookback_days)

    run = Run()
    session.add(run)
    session.flush()
    report = FetchReport(run_id=run.id)

    topics = session.scalars(
        select(Topic).where(Topic.status == TopicStatus.active).order_by(Topic.id)
    ).all()
    total = len(topics)

    for done, topic in enumerate(topics, start=1):
        progress(done - 1, total, topic.keyword)
        known_urls = set(
            session.scalars(select(Article.source_url).where(Article.topic_id == topic.id)).all()
        )
        known_titles = {
            _normalize_title(t)
            for t in session.scalars(
                select(Article.title_original).where(
                    Article.topic_id == topic.id,
                    Article.status != ArticleStatus.broken,
                )
            ).all()
        }

        for language in json.loads(topic.languages):
            stat = ChannelStat(keyword=topic.keyword, channel=language)
            report.stats.append(stat)
            try:
                entries = fetch_entries(
                    topic.keyword, language, _region_for(language, ing), timeout=timeout
                )
            except Exception as exc:  # noqa: BLE001 — one bad feed never aborts the run
                stat.error = f"{type(exc).__name__}: {exc}"
                continue
            _store_entries(
                session,
                entries,
                topic=topic,
                run=run,
                source_type=SourceType.google_news,
                known_urls=known_urls,
                known_titles=known_titles,
                cutoff=cutoff,
                stat=stat,
            )

        for source in _sources_for_topic(session, topic.id):
            stat = ChannelStat(keyword=topic.keyword, channel=source.name)
            report.stats.append(stat)
            try:
                entries = poll_feed(str(source.resolved_feed_url), source.name, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                stat.error = f"{type(exc).__name__}: {exc}"
                continue
            _store_entries(
                session,
                entries,
                topic=topic,
                run=run,
                source_type=SourceType.user_source,
                known_urls=known_urls,
                known_titles=known_titles,
                cutoff=cutoff,
                stat=stat,
            )
        session.commit()  # durable per topic — a crash/interrupt keeps prior successes
        progress(done, total, topic.keyword)

    run.finished_at = utcnow()
    run.stats = json.dumps(report.as_dict())
    return report

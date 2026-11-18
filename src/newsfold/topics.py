"""Manage the `topics` table: sync from config, or add/archive ad hoc.

`sync_topics` reconciles config/topics.yaml into the DB — match is by keyword,
case-insensitive, config is authoritative for mode + languages of topics it lists.
Topics in the DB but absent from config are left untouched but reported so the user
notices drift.

`add_topic`/`archive_topic` manage a single topic directly against the DB (e.g. from
the web UI) without touching config/topics.yaml — same DB-is-source-of-truth model
as `sources.py`. A topic added this way survives a DB reset only if also hand-added
to topics.yaml. Archiving, previously manual-only, is now available either way.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from newsfold.config import load_topics
from newsfold.models import Topic, TopicMode, TopicStatus


class TopicError(RuntimeError):
    """Raised when a topic cannot be added (duplicate keyword, invalid mode, etc.)."""


def add_topic(
    session: Session,
    keyword: str,
    *,
    mode: str = "temporary",
    languages: Sequence[str] = ("en",),
) -> Topic:
    keyword = keyword.strip()
    if not keyword:
        raise TopicError("keyword is empty")
    if mode not in {m.value for m in TopicMode}:
        raise TopicError(f"mode must be one of {sorted(m.value for m in TopicMode)}")
    languages = tuple(lang.strip() for lang in languages if lang.strip())
    if not languages:
        raise TopicError("languages must be non-empty")
    existing = session.scalar(select(Topic).where(func.lower(Topic.keyword) == keyword.lower()))
    if existing is not None:
        raise TopicError(f"a topic with keyword {keyword!r} already exists")

    topic = Topic(
        keyword=keyword,
        mode=TopicMode(mode),
        languages=json.dumps(list(languages)),
        status=TopicStatus.active,
    )
    session.add(topic)
    session.flush()
    return topic


def archive_topic(session: Session, topic_id: int) -> bool:
    topic = session.get(Topic, topic_id)
    if topic is None:
        return False
    topic.status = TopicStatus.archived
    return True


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    in_db_not_in_config: list[str] = field(default_factory=list)


def sync_topics(session: Session) -> SyncReport:
    report = SyncReport()
    existing = {t.keyword.lower(): t for t in session.scalars(select(Topic)).all()}
    config_keys: set[str] = set()

    for cfg in load_topics():
        key = cfg.keyword.lower()
        config_keys.add(key)
        languages = json.dumps(list(cfg.languages))
        mode = TopicMode(cfg.mode)
        row = existing.get(key)
        if row is None:
            session.add(
                Topic(
                    keyword=cfg.keyword,
                    mode=mode,
                    languages=languages,
                    status=TopicStatus.active,
                )
            )
            report.added.append(cfg.keyword)
            continue
        if row.mode != mode or row.languages != languages:
            row.mode = mode
            row.languages = languages
            report.updated.append(cfg.keyword)
        else:
            report.unchanged.append(cfg.keyword)

    for key, row in existing.items():
        if key not in config_keys:
            report.in_db_not_in_config.append(row.keyword)

    return report

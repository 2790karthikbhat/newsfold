"""Reader-driven article lifecycle: read/unread, archive, save, delete, and
age-based retention cleanup.

Orthogonal to `ArticleStatus` (the pipeline's own progress ladder) — see
`ReadState` in models.py. Manual actions here (mark_read, archive, delete, ...)
always honor exactly what's asked, no guardrails: the caller (CLI/web) is the
user acting deliberately. `run_cleanup` is the one automated, unattended path, so
it stays conservative by construction — see its docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from newsfold.config import load_settings
from newsfold.models import Article, ArticleStatus, ReadState
from newsfold.timeutil import utcnow


def _by_ids(session: Session, article_ids: list[int]) -> list[Article]:
    if not article_ids:
        return []
    return list(session.scalars(select(Article).where(Article.id.in_(article_ids))).all())


def mark_read(session: Session, article_ids: list[int]) -> int:
    rows = _by_ids(session, article_ids)
    for row in rows:
        row.read_state = ReadState.read
    return len(rows)


def mark_unread(session: Session, article_ids: list[int]) -> int:
    rows = _by_ids(session, article_ids)
    for row in rows:
        row.read_state = ReadState.unread
    return len(rows)


def archive(session: Session, article_ids: list[int]) -> int:
    rows = _by_ids(session, article_ids)
    for row in rows:
        row.read_state = ReadState.archived
    return len(rows)


def unarchive(session: Session, article_ids: list[int]) -> int:
    """Back to `read` — archiving something implies you were done with it, not
    that it was never seen."""
    rows = _by_ids(session, article_ids)
    for row in rows:
        row.read_state = ReadState.read
    return len(rows)


def save(session: Session, article_ids: list[int]) -> int:
    rows = _by_ids(session, article_ids)
    for row in rows:
        row.saved = True
    return len(rows)


def unsave(session: Session, article_ids: list[int]) -> int:
    rows = _by_ids(session, article_ids)
    for row in rows:
        row.saved = False
    return len(rows)


def delete(session: Session, article_ids: list[int]) -> int:
    """Hard delete — permanent, unlike archive. No guardrails: an explicit
    by-id request is always honored, saved or not."""
    rows = _by_ids(session, article_ids)
    for row in rows:
        session.delete(row)
    return len(rows)


@dataclass
class CleanupReport:
    archive_candidates: int = 0
    archived: int = 0
    delete_candidates: int = 0
    deleted: int = 0


def _age_cutoff_clause(cutoff: datetime) -> ColumnElement[bool]:
    return or_(
        Article.published_at < cutoff,
        and_(Article.published_at.is_(None), Article.fetched_at < cutoff),
    )


def run_cleanup(
    session: Session,
    *,
    dry_run: bool = False,
    archive_after_days: float | None = None,
    delete_after_days: float | None = None,
) -> CleanupReport:
    """The one automated, unattended retention path — run explicitly via
    `digest cleanup`, never silently inside `digest run`. Deliberately
    conservative: `saved` articles are never touched by either policy no matter
    how old, and auto-delete only ever considers `discarded`/`broken` rows
    (pipeline noise that was never shown to a reader) — it never removes
    anything unread, read, or archived regardless of age.

    `archive_after_days` / `delete_after_days` override `config/settings.yaml`
    `[retention]` for this call when given; otherwise read from there (0/unset
    disables that policy).
    """
    cfg = load_settings().section("retention")
    archive_after = (
        archive_after_days
        if archive_after_days is not None
        else float(cfg.get("auto_archive_after_days") or 0)
    )
    delete_after = (
        delete_after_days
        if delete_after_days is not None
        else float(cfg.get("auto_delete_after_days") or 0)
    )
    now = utcnow()
    report = CleanupReport()

    if archive_after > 0:
        cutoff = now - timedelta(days=archive_after)
        candidates = session.scalars(
            select(Article).where(
                Article.status.in_((ArticleStatus.clustered, ArticleStatus.processed)),
                Article.read_state == ReadState.unread,
                Article.saved.is_(False),
                _age_cutoff_clause(cutoff),
            )
        ).all()
        report.archive_candidates = len(candidates)
        if not dry_run:
            for row in candidates:
                row.read_state = ReadState.archived
            report.archived = len(candidates)

    if delete_after > 0:
        cutoff = now - timedelta(days=delete_after)
        candidates = session.scalars(
            select(Article).where(
                Article.status.in_((ArticleStatus.discarded, ArticleStatus.broken)),
                Article.saved.is_(False),
                _age_cutoff_clause(cutoff),
            )
        ).all()
        report.delete_candidates = len(candidates)
        if not dry_run:
            for row in candidates:
                session.delete(row)
            report.deleted = len(candidates)

    return report

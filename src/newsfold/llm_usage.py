"""Persist and read back LLM token usage (`llm_usage` table).

The only place `LLMUsage` rows are written. Stages call `flush_usage` right
before each per-item commit, so an interrupted run still records the calls it
already paid for.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from newsfold.llm.usage import UsageKey, UsageLog, UsageTotals
from newsfold.models import LLMUsage


def flush_usage(session: Session, log: UsageLog, run_id: int | None) -> None:
    """Add the log's not-yet-persisted totals to the DB (does not commit)."""
    for key, delta in log.take_pending().items():
        row = session.get(LLMUsage, log.row_ids[key]) if key in log.row_ids else None
        if row is None and run_id is not None:
            row = session.scalars(
                select(LLMUsage).where(
                    LLMUsage.run_id == run_id,
                    LLMUsage.stage == key.stage,
                    LLMUsage.provider == key.provider,
                    LLMUsage.model == key.model,
                )
            ).first()
        if row is None:
            row = LLMUsage(
                run_id=run_id,
                stage=key.stage,
                provider=key.provider,
                model=key.model,
                calls=0,
                prompt_tokens=0,
                completion_tokens=0,
                calls_without_usage=0,
            )
            session.add(row)
        row.calls += delta.calls
        row.prompt_tokens += delta.prompt_tokens
        row.completion_tokens += delta.completion_tokens
        row.calls_without_usage += delta.calls_without_usage
        session.flush()
        log.row_ids[key] = row.id


def usage_for_run(session: Session, run_id: int) -> UsageLog:
    log = UsageLog()
    for row in session.scalars(select(LLMUsage).where(LLMUsage.run_id == run_id)).all():
        log.add_totals(
            UsageKey(row.stage, row.provider, row.model),
            UsageTotals(
                calls=row.calls,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                calls_without_usage=row.calls_without_usage,
            ),
        )
    return log


def latest_run_usage(session: Session) -> tuple[int, UsageLog] | None:
    """(run_id, usage) for the most recent run that made any LLM call."""
    run_id = session.scalars(
        select(LLMUsage.run_id)
        .where(LLMUsage.run_id.is_not(None))
        .order_by(LLMUsage.run_id.desc())
        .limit(1)
    ).first()
    if run_id is None:
        return None
    return run_id, usage_for_run(session, run_id)

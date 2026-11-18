"""Time helpers. The DB (SQLite) returns naive datetimes, so the whole codebase works
in **naive UTC**: convert aware values at the ingestion boundary, compare freely after.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)

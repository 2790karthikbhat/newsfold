"""Shared ingestion primitives used by both Google News and user-source feeds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from newsfold.timeutil import to_utc_naive

USER_AGENT = "newsfold-digest/0.1 (+https://github.com/; personal use)"


@dataclass(frozen=True)
class FeedEntry:
    title: str
    link: str
    published_at: datetime | None
    source_name: str
    language: str | None  # known for Google News (query locale); often None for user feeds


def parse_published(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    year, month, day, hour, minute, second = (int(x) for x in parsed[:6])
    # feedparser's *_parsed is always UTC; store it naive-UTC like everything else.
    return to_utc_naive(datetime(year, month, day, hour, minute, second, tzinfo=UTC))


def make_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )

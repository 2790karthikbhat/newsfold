"""Build Google News RSS search URLs and parse entries with feedparser.

URL pattern:
  https://news.google.com/rss/search?q={kw}&hl={lang}-{region}&gl={region}&ceid={region}:{lang}

One request per language in topic.languages. Entries become Article rows with
status=fetched, source_type=google_news. `link` is the *undecoded* Google News blob
at this stage — resolving it to a real publisher URL is the decode stage's job (`ingest/decode.py`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import feedparser
import httpx

from newsfold.ingest.common import FeedEntry, make_client, parse_published

_BASE = "https://news.google.com/rss/search"


def build_search_url(keyword: str, lang: str, region: str) -> str:
    query = urlencode(
        {
            "q": keyword,
            "hl": f"{lang}-{region}",
            "gl": region,
            "ceid": f"{region}:{lang}",
        }
    )
    return f"{_BASE}?{query}"


def _source_name(entry: Any) -> str:
    source = entry.get("source")
    if source and source.get("title"):
        return str(source["title"]).strip()
    # Google News titles are "Headline - Publisher"; fall back to the trailing segment.
    title = str(entry.get("title", ""))
    if " - " in title:
        return title.rsplit(" - ", 1)[1].strip()
    return "unknown"


def _clean_title(entry: Any, source_name: str) -> str:
    title = str(entry.get("title", "")).strip()
    suffix = f" - {source_name}"
    if source_name != "unknown" and title.endswith(suffix):
        return title[: -len(suffix)].strip()
    return title


def parse_feed(content: bytes, language: str) -> list[FeedEntry]:
    parsed = feedparser.parse(content)
    entries: list[FeedEntry] = []
    for entry in parsed.entries:
        link = str(entry.get("link", "")).strip()
        if not link:
            continue
        source_name = _source_name(entry)
        entries.append(
            FeedEntry(
                title=_clean_title(entry, source_name),
                link=link,
                published_at=parse_published(entry),
                source_name=source_name,
                language=language,
            )
        )
    return entries


def fetch_entries(
    keyword: str,
    lang: str,
    region: str,
    *,
    timeout: float = 20.0,
    client: httpx.Client | None = None,
) -> list[FeedEntry]:
    url = build_search_url(keyword, lang, region)
    owns_client = client is None
    client = client or make_client(timeout)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return parse_feed(resp.content, lang)
    finally:
        if owns_client:
            client.close()

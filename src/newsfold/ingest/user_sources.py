"""Website URL -> resolved feed URL, feed autodiscovery.

Discovery order (stop at first hit):
  1. input already looks like a feed (path suffix, or feed content-type on fetch)
  2. <link rel="alternate" type="application/(rss|atom)+xml"> in the page <head>
  3. common feed paths against the domain
  4. nothing resolved -> the source is marked `broken` and surfaced to the user

feedparser validates that the resolved URL actually parses as a feed before it is
saved `active`. User-added sources default to tier-1 for scoring — the
user chose them deliberately.

Hand-rolled with httpx + lxml.html: feedsearch-crawler pulls in aiohttp/uvloop
(which will not build here) and the four steps are small.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from lxml import etree as lxml_etree
from lxml import html as lxml_html

from newsfold.ingest.common import FeedEntry, make_client, parse_published

_FEED_CONTENT_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml")
_FEED_PATH_HINTS = ("/feed", "/feed/", "/rss", "/rss.xml", "/atom.xml", "/index.xml")
_COMMON_PATHS = (
    "/feed",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/atom.xml",
    "/feeds/posts/default",
    "/index.xml",
)


@dataclass(frozen=True)
class DiscoveryResult:
    ok: bool
    feed_url: str | None = None
    feed_title: str | None = None
    error: str | None = None


def _looks_like_feed_path(url: str) -> bool:
    path = urlparse(url).path.lower().rstrip("/")
    return url.lower().endswith(".xml") or any(
        path.endswith(h.rstrip("/")) for h in _FEED_PATH_HINTS
    )


def _valid_feed(content: bytes) -> tuple[bool, str | None]:
    parsed = feedparser.parse(content)
    has_entries = bool(parsed.entries)
    has_meta = bool(parsed.feed.get("title") or parsed.feed.get("link"))
    if parsed.bozo and not has_entries:
        return False, None
    if not (has_entries or has_meta):
        return False, None
    title = parsed.feed.get("title")
    return True, str(title).strip() if title else None


def _autodiscover_links(page: bytes, base_url: str) -> list[str]:
    try:
        doc = lxml_html.fromstring(page)
    except (ValueError, lxml_etree.LxmlError):
        return []
    found: list[str] = []
    for link in doc.xpath("//link[@rel='alternate'][@href]"):
        ctype = (link.get("type") or "").lower()
        if any(ctype.startswith(ft) for ft in _FEED_CONTENT_TYPES):
            found.append(urljoin(base_url, str(link.get("href"))))
    return found


def _try_url(client: httpx.Client, url: str) -> tuple[bool, str | None]:
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError:
        return False, None
    return _valid_feed(resp.content)


def discover_feed(
    input_url: str,
    *,
    timeout: float = 20.0,
    client: httpx.Client | None = None,
) -> DiscoveryResult:
    if "://" not in input_url:
        input_url = "https://" + input_url
    parsed = urlparse(input_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    owns_client = client is None
    client = client or make_client(timeout)
    try:
        # 1. already a feed?
        try:
            resp = client.get(input_url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return DiscoveryResult(ok=False, error=f"could not fetch {input_url}: {exc}")

        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if _looks_like_feed_path(input_url) or ctype in _FEED_CONTENT_TYPES:
            ok, title = _valid_feed(resp.content)
            if ok:
                return DiscoveryResult(ok=True, feed_url=input_url, feed_title=title)

        # 2. <head> autodiscovery
        for candidate in _autodiscover_links(resp.content, input_url):
            ok, title = _try_url(client, candidate)
            if ok:
                return DiscoveryResult(ok=True, feed_url=candidate, feed_title=title)

        # 3. common paths
        for path in _COMMON_PATHS:
            candidate = urljoin(origin, path)
            ok, title = _try_url(client, candidate)
            if ok:
                return DiscoveryResult(ok=True, feed_url=candidate, feed_title=title)
    finally:
        if owns_client:
            client.close()

    return DiscoveryResult(ok=False, error="no feed found (autodiscovery + common paths exhausted)")


def poll_feed(
    feed_url: str,
    source_name: str,
    *,
    timeout: float = 20.0,
    client: httpx.Client | None = None,
) -> list[FeedEntry]:
    owns_client = client is None
    client = client or make_client(timeout)
    try:
        resp = client.get(feed_url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    finally:
        if owns_client:
            client.close()

    feed_lang = (parsed.feed.get("language") or "")[:2].lower() or None
    entries: list[FeedEntry] = []
    for entry in parsed.entries:
        link = str(entry.get("link", "")).strip()
        title = str(entry.get("title", "")).strip()
        if not link or not title:
            continue
        entries.append(
            FeedEntry(
                title=title,
                link=link,
                published_at=parse_published(entry),
                source_name=source_name,
                language=feed_lang,
            )
        )
    return entries

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from newsfold.ingest.user_sources import discover_feed, poll_feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Tuoi Tre - Sports</title>
  <language>vi</language>
  <item>
    <title>Doi tuyen Viet Nam thang</title>
    <link>https://tuoitre.vn/story-1.htm</link>
    <pubDate>Mon, 08 Sep 2026 09:00:00 GMT</pubDate>
  </item>
  <item><title>No link here</title></item>
</channel></rss>"""

HTML_WITH_FEED = b"""<html><head>
  <link rel="alternate" type="application/rss+xml" href="/feed.xml">
</head><body>hi</body></html>"""

HTML_NO_FEED = b"<html><head><title>nothing</title></head><body>x</body></html>"


def _client(routes: dict[str, tuple[int, bytes, str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        if key not in routes:
            return httpx.Response(404)
        status, body, ctype = routes[key]
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_discover_direct_feed_url() -> None:
    client = _client({"https://tuoitre.vn/rss/sports.xml": (200, RSS, "application/rss+xml")})
    result = discover_feed("https://tuoitre.vn/rss/sports.xml", client=client)
    assert result.ok
    assert result.feed_url == "https://tuoitre.vn/rss/sports.xml"
    assert result.feed_title == "Tuoi Tre - Sports"


def test_discover_via_head_autodiscovery() -> None:
    client = _client(
        {
            "https://blog.example/": (200, HTML_WITH_FEED, "text/html"),
            "https://blog.example/feed.xml": (200, RSS, "application/rss+xml"),
        }
    )
    result = discover_feed("https://blog.example/", client=client)
    assert result.ok
    assert result.feed_url == "https://blog.example/feed.xml"


def test_discover_via_common_path() -> None:
    client = _client(
        {
            "https://blog.example/": (200, HTML_NO_FEED, "text/html"),
            "https://blog.example/feed": (200, RSS, "application/rss+xml"),
        }
    )
    result = discover_feed("https://blog.example/", client=client)
    assert result.ok
    assert result.feed_url == "https://blog.example/feed"


def test_discover_gives_up() -> None:
    client = _client({"https://blog.example/": (200, HTML_NO_FEED, "text/html")})
    result = discover_feed("https://blog.example/", client=client)
    assert not result.ok
    assert result.error is not None


def test_discover_unreachable() -> None:
    client = _client({})  # everything 404s
    result = discover_feed("https://nope.example/", client=client)
    assert not result.ok


def test_poll_feed_parses_entries() -> None:
    client = _client({"https://tuoitre.vn/feed": (200, RSS, "application/rss+xml")})
    entries = poll_feed("https://tuoitre.vn/feed", "Tuoi Tre", client=client)
    assert len(entries) == 1  # second item has no link
    e = entries[0]
    assert e.link == "https://tuoitre.vn/story-1.htm"
    assert e.source_name == "Tuoi Tre"
    assert e.language == "vi"
    assert e.published_at == datetime(2026, 9, 8, 9, 0, 0)  # naive UTC


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/feed.xml", True),
        ("https://x.com/blog/feed", True),
        ("https://x.com/rss/", True),
        ("https://x.com/about", False),
    ],
)
def test_looks_like_feed_path(url: str, expected: bool) -> None:
    from newsfold.ingest.user_sources import _looks_like_feed_path

    assert _looks_like_feed_path(url) is expected

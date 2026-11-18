from __future__ import annotations

from datetime import datetime

from newsfold.ingest.google_news import build_search_url, parse_feed

_FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Alpine keeps Colapinto for 2027 - The Race</title>
    <link>https://news.google.com/rss/articles/BLOB1</link>
    <pubDate>Wed, 27 Aug 2026 14:17:41 GMT</pubDate>
    <source url="https://the-race.com">The Race</source>
  </item>
  <item>
    <title>Headline with no source element</title>
    <link>https://news.google.com/rss/articles/BLOB2</link>
    <pubDate>Wed, 27 Aug 2026 07:40:02 GMT</pubDate>
  </item>
  <item>
    <title>Missing link is skipped - Somebody</title>
  </item>
</channel></rss>"""


def test_build_search_url_encodes_query_and_locale() -> None:
    url = build_search_url("vegetarian food Vietnam", "vi", "VN")
    assert url == (
        "https://news.google.com/rss/search?"
        "q=vegetarian+food+Vietnam&hl=vi-VN&gl=VN&ceid=VN%3Avi"
    )


def test_parse_feed_extracts_and_cleans_entries() -> None:
    entries = parse_feed(_FEED, "en")
    assert len(entries) == 2  # third item has no link

    first = entries[0]
    assert first.title == "Alpine keeps Colapinto for 2027"  # publisher suffix stripped
    assert first.source_name == "The Race"
    assert first.link == "https://news.google.com/rss/articles/BLOB1"
    assert first.published_at == datetime(2026, 8, 27, 14, 17, 41)  # naive UTC
    assert first.language == "en"


def test_parse_feed_falls_back_when_no_source_element() -> None:
    second = parse_feed(_FEED, "en")[1]
    assert second.source_name == "unknown"
    assert second.title == "Headline with no source element"

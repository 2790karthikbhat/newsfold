from __future__ import annotations

from datetime import timedelta

import pytest

from newsfold.models import Article, SourceType
from newsfold.rank.score import (
    registrable_domain,
    representative,
    score_cluster,
    tier1_domains_for,
)
from newsfold.timeutil import utcnow

TIERS = {"default": ["vnexpress.net"], "by_topic": {"F1": ["formula1.com"]}}


def _art(url: str, *, published_at=None, title="t") -> Article:
    return Article(
        source_url=url,
        source_name=url,
        source_type=SourceType.google_news,
        title_original=title,
        published_at=published_at,
    )


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.chosun.com", "chosun.com"),
        ("biz.chosun.com", "chosun.com"),
        ("vnexpress.net", "vnexpress.net"),
        ("en.sggp.org.vn", "sggp.org.vn"),
        ("www.vietnam.vn", "vietnam.vn"),
        ("thethao.vnexpress.net", "vnexpress.net"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


def test_tier1_domains_for_merges_default_and_topic() -> None:
    assert tier1_domains_for(TIERS, "F1") == {"vnexpress.net", "formula1.com"}
    assert tier1_domains_for(TIERS, "sports Vietnam") == {"vnexpress.net"}


def test_score_counts_distinct_domains_and_tier1_bonus() -> None:
    now = utcnow()
    members = [
        _art("https://a.com/1", published_at=now),
        _art("https://a.com/2", published_at=now),  # same domain — not double counted
        _art("https://b.com/1", published_at=now),
    ]
    score, distinct, has_tier1 = score_cluster(
        members, tier1_domains={"vnexpress.net"}, half_life_hours=24, tier1_bonus=2.0, now=now
    )
    assert distinct == 2
    assert has_tier1 is False
    assert 2.9 < score <= 3.0  # 2 domains + recency (~1, fresh), no tier bonus


def test_score_adds_tier1_bonus_when_present() -> None:
    now = utcnow()
    members = [_art("https://vnexpress.net/x", published_at=now)]
    score, _, has_tier1 = score_cluster(
        members, tier1_domains={"vnexpress.net"}, half_life_hours=24, tier1_bonus=2.0, now=now
    )
    assert has_tier1 is True
    assert score > 3.0  # 1 domain + 2 bonus + recency


def test_score_recency_decays() -> None:
    now = utcnow()
    fresh = [_art("https://a.com/1", published_at=now)]
    old = [_art("https://a.com/1", published_at=now - timedelta(hours=48))]
    s_fresh, *_ = score_cluster(
        fresh, tier1_domains=set(), half_life_hours=24, tier1_bonus=2.0, now=now
    )
    s_old, *_ = score_cluster(
        old, tier1_domains=set(), half_life_hours=24, tier1_bonus=2.0, now=now
    )
    assert s_fresh > s_old
    assert s_old - 1.0 < 0.3  # 48h = 2 half-lives -> recency ~0.25


def test_representative_prefers_tier1_then_recency() -> None:
    now = utcnow()
    a = _art("https://blog.com/1", published_at=now, title="newest non-tier1")
    b = _art("https://vnexpress.net/1", published_at=now - timedelta(days=1), title="older tier1")
    assert representative([a, b], {"vnexpress.net"}) is b

    c = _art("https://x.com/1", published_at=now - timedelta(hours=1), title="older")
    d = _art("https://y.com/1", published_at=now, title="newer")
    assert representative([c, d], set()) is d

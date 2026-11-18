"""Cluster scoring: source corroboration + tier-1 bonus + recency tiebreak.

    score = distinct_source_domains
          + tier1_bonus            (if any tier-1 domain is in the cluster)
          + recency                (exp decay in (0, 1] — only ever breaks ties)

`rank_score` lands on the cluster's representative article; `run_cluster` keeps the
top N per topic and the LLM ranking pass ranks those.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from newsfold.models import Article

# second-level "public" labels that sit under a ccTLD (chosun.com stays chosun.com,
# but vnexpress.com.vn collapses to vnexpress.com.vn, bbc.co.uk to bbc.co.uk)
_SECOND_LEVEL = {"com", "net", "org", "gov", "edu", "co", "ac", "gob", "or"}


def registrable_domain(host: str) -> str:
    """Best-effort eTLD+1 so `biz.chosun.com` and `www.chosun.com` count as one source."""
    labels = host.lower().strip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _domain(url: str) -> str:
    return registrable_domain(urlparse(url).netloc)


def _is_tier1(domain: str, tier1_domains: set[str]) -> bool:
    return any(domain == d or domain.endswith(f".{d}") for d in tier1_domains)


def tier1_domains_for(tiers_cfg: dict[str, Any], topic_keyword: str) -> set[str]:
    domains = set(tiers_cfg.get("default") or [])
    by_topic = tiers_cfg.get("by_topic") or {}
    domains.update(by_topic.get(topic_keyword) or [])
    return {d.lower() for d in domains}


def _recency(newest: datetime | None, half_life_hours: float, now: datetime) -> float:
    if newest is None:
        return 0.0
    age_hours = max((now - newest).total_seconds() / 3600.0, 0.0)
    return math.exp(-age_hours * math.log(2) / half_life_hours)


def score_cluster(
    members: list[Article],
    *,
    tier1_domains: set[str],
    half_life_hours: float,
    tier1_bonus: float,
    now: datetime,
) -> tuple[float, int, bool]:
    domains = {_domain(m.source_url) for m in members if m.source_url}
    distinct_sources = len(domains)
    has_tier1 = any(_is_tier1(d, tier1_domains) for d in domains)

    dates = [m.published_at for m in members if m.published_at is not None]
    recency = _recency(max(dates), half_life_hours, now) if dates else 0.0

    score = distinct_sources + (tier1_bonus if has_tier1 else 0.0) + recency
    return score, distinct_sources, has_tier1


def representative(members: list[Article], tier1_domains: set[str]) -> Article:
    """Pick the article that best stands in for the cluster: tier-1 first, then newest."""

    def key(article: Article) -> tuple[int, float]:
        is_tier1 = (
            _is_tier1(_domain(article.source_url), tier1_domains) if article.source_url else 0
        )
        recency = article.published_at.timestamp() if article.published_at else 0.0
        return (int(is_tier1), recency)

    return max(members, key=key)

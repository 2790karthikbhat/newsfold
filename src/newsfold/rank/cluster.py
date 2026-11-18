"""Group entries about the same story: title-token overlap + publish window.

Pure keyword-overlap clustering, no LLM. Per (topic, language) — cross-language merge
(a vi article and its en counterpart) is deliberately out of scope for v1; the LLM
rank pass collapses semantic duplicates that slip through.

`run_cluster` only touches articles with `cluster_id IS NULL`, so within one
`digest run` it clusters exactly the rows `fetch` just added, and a standalone re-run
is a no-op. Known v1 limitation: an article that arrives in a later run and matches an
already-clustered story forms its own singleton instead of joining it.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from newsfold.config import load_settings, load_tiers
from newsfold.models import Article, ArticleStatus, SourceType, Topic, TopicStatus
from newsfold.rank.score import representative, score_cluster, tier1_domains_for
from newsfold.timeutil import utcnow

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    # english
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "and",
    "or",
    "is",
    "are",
    "was",
    "were",
    "be",
    "with",
    "by",
    "from",
    "as",
    "it",
    "its",
    "this",
    "that",
    "new",
    "after",
    "over",
    "into",
    "out",
    "up",
    "his",
    "her",
    "their",
    # vietnamese (diacritics already stripped)
    "va",
    "cua",
    "cac",
    "mot",
    "nhung",
    "da",
    "se",
    "la",
    "o",
    "cho",
    "voi",
    "trong",
    "khong",
    "co",
    "den",
    "ve",
    "nay",
    "duoc",
    "bi",
}


@dataclass
class TopicClusterStat:
    keyword: str
    language: str
    articles: int = 0
    clusters: int = 0


@dataclass
class ClusterSummary:
    cluster_id: str
    keyword: str
    language: str
    size: int
    distinct_sources: int
    has_tier1: bool
    score: float
    representative_title: str
    survivor: bool


@dataclass
class ClusterReport:
    stats: list[TopicClusterStat] = field(default_factory=list)
    summaries: list[ClusterSummary] = field(default_factory=list)

    @property
    def total_clusters(self) -> int:
        return sum(s.clusters for s in self.stats)

    @property
    def total_survivors(self) -> int:
        return sum(1 for c in self.summaries if c.survivor)


def _strip_diacritics(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokenize(title: str) -> set[str]:
    base = _strip_diacritics(title).lower()
    return {t for t in _WORD_RE.findall(base) if len(t) >= 2 and t not in _STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _time_within(a: Article, b: Article, window: timedelta) -> bool:
    if a.published_at is None or b.published_at is None:
        return True
    return abs(a.published_at - b.published_at) <= window


def cluster_articles(
    articles: list[Article], *, threshold: float, window: timedelta
) -> list[list[Article]]:
    """Single-link agglomeration over one (topic, language) group."""
    tokens = [tokenize(a.title_original) for a in articles]
    parent = list(range(len(articles)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            if _time_within(articles[i], articles[j], window) and (
                jaccard(tokens[i], tokens[j]) >= threshold
            ):
                parent[find(i)] = find(j)

    groups: dict[int, list[Article]] = {}
    for idx, article in enumerate(articles):
        groups.setdefault(find(idx), []).append(article)
    return list(groups.values())


def _unclustered_for_topic(session: Session, topic_id: int) -> list[Article]:
    return list(
        session.scalars(
            select(Article)
            .where(
                Article.topic_id == topic_id,
                Article.cluster_id.is_(None),
                or_(
                    and_(
                        Article.source_type == SourceType.google_news,
                        Article.status == ArticleStatus.decoded,
                    ),
                    and_(
                        Article.source_type == SourceType.user_source,
                        Article.status == ArticleStatus.fetched,
                    ),
                ),
            )
            .order_by(Article.id)
        ).all()
    )


def run_cluster(session: Session) -> ClusterReport:
    settings = load_settings()
    clustering = settings.section("clustering")
    ranking = settings.section("ranking")
    threshold = float(clustering.get("same_story_title_jaccard", 0.4))
    window = timedelta(days=float(clustering.get("publish_window_days", 1)))
    half_life = float(clustering.get("recency_half_life_hours", 24))
    tier1_bonus = float(ranking.get("tier1_bonus", 2.0))
    keep = int(ranking.get("candidate_clusters_per_topic", 18))
    tiers = load_tiers()
    now = utcnow()

    report = ClusterReport()
    topics = session.scalars(
        select(Topic).where(Topic.status == TopicStatus.active).order_by(Topic.id)
    ).all()

    for topic in topics:
        tier1 = tier1_domains_for(tiers, topic.keyword)
        pool = _unclustered_for_topic(session, topic.id)
        by_language: dict[str, list[Article]] = {}
        for article in pool:
            by_language.setdefault(article.language_original or "?", []).append(article)

        scored: list[tuple[float, ClusterSummary, Article]] = []
        for language, group in by_language.items():
            clusters = cluster_articles(group, threshold=threshold, window=window)
            report.stats.append(
                TopicClusterStat(
                    keyword=topic.keyword,
                    language=language,
                    articles=len(group),
                    clusters=len(clusters),
                )
            )
            for members in clusters:
                cid = uuid.uuid4().hex[:16]
                score, distinct_sources, has_tier1 = score_cluster(
                    members,
                    tier1_domains=tier1,
                    half_life_hours=half_life,
                    tier1_bonus=tier1_bonus,
                    now=now,
                )
                rep = representative(members, tier1)
                for member in members:
                    member.cluster_id = cid
                rep.rank_score = score
                scored.append(
                    (
                        score,
                        ClusterSummary(
                            cluster_id=cid,
                            keyword=topic.keyword,
                            language=language,
                            size=len(members),
                            distinct_sources=distinct_sources,
                            has_tier1=has_tier1,
                            score=score,
                            representative_title=rep.title_original,
                            survivor=False,
                        ),
                        rep,
                    )
                )

        scored.sort(key=lambda t: t[0], reverse=True)
        for rank_index, (_, summary, rep) in enumerate(scored):
            if rank_index < keep:
                summary.survivor = True
                rep.status = ArticleStatus.clustered
            report.summaries.append(summary)

    return report

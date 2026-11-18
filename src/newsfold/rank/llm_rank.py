"""Newsworthiness ranking — one batched LLM call per topic per run.

Takes the cluster representatives that survived cluster scoring, asks the model to pick
and order the most newsworthy/relevant ones, and records `llm_rank` + `llm_reason`
on the winners. Representatives the model leaves out are `discarded`.

One call per topic — never per article (CLAUDE.md budget invariant).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from newsfold.config import load_settings
from newsfold.llm.base import LLMError
from newsfold.llm.client import complete_json, describe
from newsfold.llm.usage import UsageLog
from newsfold.llm_usage import flush_usage
from newsfold.models import Article, ArticleStatus, Topic, TopicStatus
from newsfold.progress import ProgressCallback, noop

_SYSTEM = (
    "You are the editor of a personal news digest. Given a numbered list of candidate "
    "stories for one topic, pick the most newsworthy and genuinely relevant ones and "
    "rank them best first. Prefer real developments over routine or promotional items. "
    'Return only JSON: {"ranked": [{"index": <number>, "reason": "<one short line>"}]}. '
    "Include at most the requested number of entries; drop anything not worth the "
    "reader's time."
)


@dataclass
class TopicRankStat:
    keyword: str
    candidates: int = 0
    promoted: int = 0
    discarded: int = 0
    skipped: bool = False
    error: str | None = None


@dataclass
class RankReport:
    provider: str = ""
    stats: list[TopicRankStat] = field(default_factory=list)
    usage: UsageLog = field(default_factory=UsageLog, repr=False)

    @property
    def total_promoted(self) -> int:
        return sum(s.promoted for s in self.stats)


def _candidate_block(articles: Sequence[Article]) -> str:
    lines = []
    for i, a in enumerate(articles, start=1):
        lead = (a.body_original or "").strip().replace("\n", " ")[:200]
        lines.append(f"{i}. {a.title_original}" + (f" — {lead}" if lead else ""))
    return "\n".join(lines)


def _parse_ranked(data: dict[str, object], n: int) -> list[tuple[int, str]]:
    raw = data.get("ranked")
    if not isinstance(raw, list):
        raise ValueError("missing 'ranked' array")
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= idx <= n and idx not in seen:
            seen.add(idx)
            out.append((idx, str(item.get("reason") or "").strip()))
    return out


def run_llm_rank(
    session: Session,
    *,
    dry_run: bool = False,
    progress: ProgressCallback = noop,
    run_id: int | None = None,
) -> RankReport:
    settings = load_settings()
    ranking = settings.section("ranking")
    max_candidates = int(ranking.get("candidate_clusters_per_topic", 18))
    keep = int(ranking.get("keep_per_topic", 8))

    report = RankReport(provider=describe("rank"))
    topics = session.scalars(
        select(Topic).where(Topic.status == TopicStatus.active).order_by(Topic.id)
    ).all()
    total = len(topics)

    for done, topic in enumerate(topics, start=1):
        progress(done - 1, total, topic.keyword)
        stat = _rank_topic(
            session,
            topic,
            keep=keep,
            max_candidates=max_candidates,
            dry_run=dry_run,
            usage=report.usage,
        )
        report.stats.append(stat)
        flush_usage(session, report.usage, run_id)
        session.commit()  # durable per topic — a crash/interrupt keeps prior successes
        progress(done, total, topic.keyword)

    return report


def _rank_topic(
    session: Session,
    topic: Topic,
    *,
    keep: int,
    max_candidates: int,
    dry_run: bool,
    usage: UsageLog | None = None,
) -> TopicRankStat:
    candidates = session.scalars(
        select(Article)
        .where(Article.topic_id == topic.id, Article.status == ArticleStatus.clustered)
        .order_by(Article.rank_score.desc().nullslast(), Article.id)
        .limit(max_candidates)
    ).all()
    stat = TopicRankStat(keyword=topic.keyword, candidates=len(candidates))

    if len(candidates) <= 1:
        stat.skipped = True
        return stat

    user = (
        f"Topic: {topic.keyword}\n"
        f"Return at most {keep} stories.\n\n"
        f"{_candidate_block(candidates)}"
    )
    try:
        data = complete_json(system=_SYSTEM, user=user, task="rank", dry_run=dry_run, usage=usage)
    except LLMError as exc:
        stat.error = str(exc)
        return stat
    if dry_run:
        stat.skipped = True
        return stat

    try:
        ranked = _parse_ranked(data, len(candidates))
    except (ValueError, json.JSONDecodeError) as exc:
        stat.error = f"bad ranking response: {exc}"
        return stat

    chosen = {idx for idx, _ in ranked[:keep]}
    for position, (idx, reason) in enumerate(ranked[:keep], start=1):
        article = candidates[idx - 1]
        article.llm_rank = position
        article.llm_reason = reason
        stat.promoted += 1
    for i, article in enumerate(candidates, start=1):
        if i not in chosen:
            article.status = ArticleStatus.discarded
            article.llm_reason = article.llm_reason or "not selected by ranking"
            stat.discarded += 1

    return stat

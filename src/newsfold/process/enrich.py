"""Per-article classify + translate + summarize — one LLM call each.

Runs on ranked cluster survivors (`status == clustered`). One structured call returns the
relevance verdict, detected language, translated title, a short summary and the
translated body. Irrelevant articles are `discarded`; the rest become `processed`.

Budget: one call per surviving article, capped by `processing.max_articles_per_run`.
A failed call leaves the row `clustered` so the next run retries it — one bad
article never aborts the run. Each article's outcome is committed immediately
(not batched for the whole run), so a crash or Ctrl+C keeps every success made
so far — the next run only considers what's still `clustered`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from newsfold.config import load_settings
from newsfold.llm.base import LLMError
from newsfold.llm.client import complete_json, describe
from newsfold.llm.usage import UsageLog
from newsfold.llm_usage import flush_usage
from newsfold.models import Article, ArticleStatus
from newsfold.progress import ProgressCallback, noop

_SYSTEM = (
    "You process one news article for a personal reading digest. You judge whether it "
    "is genuinely about the given topic, detect its language, translate its title and "
    "body into the target language, and write a 2–3 sentence summary in the target "
    "language. Return only a JSON object with keys: is_relevant (boolean), reason "
    "(short string), language (ISO 639-1 code of the source), title_translated (string), "
    "summary (string), body_translated (string or null)."
)

_BODY_CAP = 6000  # keep prompts bounded; the lead of an article carries the substance


@dataclass
class EnrichResult:
    is_relevant: bool
    reason: str
    language: str | None
    title_translated: str
    summary: str
    body_translated: str | None


@dataclass
class EnrichReport:
    provider: str = ""
    considered: int = 0
    enriched: int = 0
    discarded: int = 0
    failed: int = 0
    planned: int = 0  # dry-run only
    failures: list[str] = field(default_factory=list)
    usage: UsageLog = field(default_factory=UsageLog, repr=False)


def _build_user_prompt(article: Article, topic_keyword: str, target_language: str) -> str:
    body = (article.body_original or "").strip()
    if article.headline_only or not body:
        body_block = "(no full text available — summarize from the title)"
    else:
        body_block = body[:_BODY_CAP]
    return (
        f"Topic: {topic_keyword}\n"
        f"Target language: {target_language}\n"
        f"Original title: {article.title_original}\n\n"
        f"Original body:\n{body_block}"
    )


def _parse(data: dict[str, object]) -> EnrichResult:
    return EnrichResult(
        is_relevant=bool(data.get("is_relevant")),
        reason=str(data.get("reason") or "").strip(),
        language=(
            (str(data["language"]).strip().lower()[:8] or None) if data.get("language") else None
        ),
        title_translated=str(data.get("title_translated") or "").strip(),
        summary=str(data.get("summary") or "").strip(),
        body_translated=(
            str(data["body_translated"]).strip()
            if data.get("body_translated") not in (None, "", "null")
            else None
        ),
    )


def enrich_article(
    article: Article,
    topic_keyword: str,
    target_language: str,
    *,
    dry_run: bool = False,
    usage: UsageLog | None = None,
) -> EnrichResult | None:
    data = complete_json(
        system=_SYSTEM,
        user=_build_user_prompt(article, topic_keyword, target_language),
        task="enrich",
        dry_run=dry_run,
        usage=usage,
    )
    if dry_run:
        return None
    return _parse(data)


def run_enrich(
    session: Session,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    progress: ProgressCallback = noop,
    run_id: int | None = None,
) -> EnrichReport:
    settings = load_settings()
    target_language = settings.target_language
    cap = int(settings.section("processing").get("max_articles_per_run", 60))
    ceiling = min(cap, limit) if limit is not None else cap

    articles = session.scalars(
        select(Article)
        .where(Article.status == ArticleStatus.clustered)
        .order_by(
            Article.llm_rank.asc().nullslast(),
            Article.rank_score.desc().nullslast(),
            Article.id,
        )
        .limit(ceiling)
    ).all()

    report = EnrichReport(provider=describe("enrich"), considered=len(articles))
    total = len(articles)

    for done, article in enumerate(articles, start=1):
        keyword = article.topic.keyword
        note = f"{keyword}: {article.title_original[:32]}"
        progress(done - 1, total, note)
        try:
            result = enrich_article(
                article, keyword, target_language, dry_run=dry_run, usage=report.usage
            )
        except LLMError as exc:
            report.failed += 1
            report.failures.append(f"[{article.id}] {exc}")
        else:
            if dry_run or result is None:
                report.planned += 1
            elif not result.is_relevant:
                article.status = ArticleStatus.discarded
                article.llm_reason = result.reason or "off-topic"
                report.discarded += 1
            else:
                article.title_translated = result.title_translated or article.title_original
                article.summary = result.summary
                article.body_translated = result.body_translated
                if result.language and not article.language_original:
                    article.language_original = result.language
                article.status = ArticleStatus.processed
                report.enriched += 1
        flush_usage(session, report.usage, run_id)
        session.commit()  # durable per article — a crash/interrupt keeps prior successes
        progress(done, total, note)

    return report

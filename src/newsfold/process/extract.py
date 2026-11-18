"""Full article text via trafilatura — the non-LLM half of processing.

Runs on ranked cluster survivors (`status == clustered`). On success the body lands in
`Article.body_original`. On failure/paywall/JS-wall the article is flagged
`headline_only = True` and still moves downstream — the later summarize step works
from the title + whatever text we have rather than dropping a top-ranked story.
This is distinct from `broken`, which means the row is unusable. Each article is
committed immediately, so a crash or Ctrl+C keeps every success made so far.

Like decode, `extract_article` is a pure HTTP+parse call with no DB access, so it
can run concurrently in a thread pool (`processing.extract_concurrency`). Unlike
decode there's no single shared endpoint to 429 us — survivors' URLs scatter across
many publisher domains — but several survivors can still share one outlet, so this
stays bounded rather than unbounded.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import httpx
import trafilatura
from sqlalchemy import select
from sqlalchemy.orm import Session

from newsfold.config import load_settings
from newsfold.ingest.common import make_client
from newsfold.models import Article, ArticleStatus
from newsfold.progress import ProgressCallback, noop

_MIN_CHARS = 250  # shorter than this is almost always a nav stub / consent wall


@dataclass(frozen=True)
class ExtractResult:
    ok: bool
    text: str | None = None
    error: str | None = None


@dataclass
class ExtractReport:
    considered: int = 0
    extracted: int = 0
    headline_only: int = 0
    failures: list[str] = field(default_factory=list)


def extract_article(
    url: str,
    *,
    timeout: float = 20.0,
    client: httpx.Client | None = None,
) -> ExtractResult:
    owns_client = client is None
    client = client or make_client(timeout)
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return ExtractResult(ok=False, error=f"fetch failed: {exc}")
    finally:
        if owns_client:
            client.close()

    text = trafilatura.extract(
        resp.text,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not text or len(text.strip()) < _MIN_CHARS:
        return ExtractResult(ok=False, error="no usable article text extracted")
    return ExtractResult(ok=True, text=text.strip())


def _apply_result(article: Article, result: ExtractResult, report: ExtractReport) -> None:
    if result.ok and result.text:
        article.body_original = result.text
        report.extracted += 1
    else:
        article.headline_only = True
        report.headline_only += 1
        report.failures.append(f"[{article.id}] {result.error}")


def run_extract(
    session: Session,
    *,
    limit: int | None = None,
    progress: ProgressCallback = noop,
    concurrency: int | None = None,
) -> ExtractReport:
    cfg = load_settings().section("processing")
    timeout = float(cfg.get("extract_timeout_sec", 20))
    workers = max(
        1, int(concurrency if concurrency is not None else cfg.get("extract_concurrency", 1))
    )

    stmt = (
        select(Article)
        .where(
            Article.status == ArticleStatus.clustered,
            Article.body_original.is_(None),
            Article.headline_only.is_(False),
        )
        .order_by(Article.rank_score.desc().nullslast(), Article.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    articles = session.scalars(stmt).all()
    report = ExtractReport(considered=len(articles))
    total = len(articles)

    if workers <= 1:
        for done, article in enumerate(articles, start=1):
            result = extract_article(article.source_url, timeout=timeout)
            _apply_result(article, result, report)
            session.commit()  # durable per article — a crash/interrupt keeps prior successes
            progress(done, total, article.source_name)
    else:
        # extract_article is pure HTTP + parse, no DB access — safe off the main
        # thread. Every mutation (_apply_result, commit, progress) still happens
        # here, on the session's one owning thread.
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(extract_article, article.source_url, timeout=timeout): article
                for article in articles
            }
            for future in as_completed(futures):
                article = futures[future]
                try:
                    result = future.result()
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — a worker-thread failure is still headline-only, not fatal
                    result = ExtractResult(ok=False, error=f"{type(exc).__name__}: {exc}")
                _apply_result(article, result, report)
                session.commit()
                done += 1
                progress(done, total, article.source_name)

    return report

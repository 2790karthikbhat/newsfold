"""Resolve Google News redirect blobs to real publisher URLs.

`news.google.com/rss/articles/CBMi…` links are encrypted redirects, not real URLs.
Everything downstream (dedupe by URL, tier lookup, full-text fetch) needs the real
publisher URL, so this runs right after `fetch` and before anything else.

- Each decode is an HTTP round trip plus `interval` seconds of enforced spacing
  (config: ingestion.decode_request_interval_sec) — Google's endpoint 429s under
  load, so that per-request pacing never changes. What's configurable is how many
  of these (fetch page → sleep(interval) → batchexecute) sequences run at once:
  `ingestion.decode_concurrency` (default 1 = the old fully-serial behavior) runs
  N of them in a thread pool. Concurrency speeds up wall-clock time without
  shortening the pacing of any single request, but it does raise the aggregate
  request rate Google sees — raise it gradually and drop back to 1 if you start
  seeing decode failures cluster (a sign of 429s).
- Decode failure is a normal outcome: the article is marked `broken` and the run
  continues (CLAUDE.md invariant) — including a failure inside a worker thread.
- After decoding, blobs that resolved to the same real URL within a topic are
  collapsed — the earliest row wins, the rest are deleted (they carry no enrichment
  yet).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from googlenewsdecoder import gnewsdecoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from newsfold.config import load_settings
from newsfold.models import Article, ArticleStatus, SourceType
from newsfold.progress import ProgressCallback, noop

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "cmpid",
    "__twitter_impression",
    "spm",
}


@dataclass(frozen=True)
class DecodeResult:
    ok: bool
    url: str | None = None
    error: str | None = None


@dataclass
class DecodeReport:
    considered: int = 0
    decoded: int = 0
    broken: int = 0
    deduped: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "considered": self.considered,
            "decoded": self.decoded,
            "broken": self.broken,
            "deduped": self.deduped,
            "failures": self.failures[:20],
        }


def normalize_url(url: str) -> str:
    """Drop fragments and tracking query params so dedupe sees the same story once."""
    parts = urlparse(url.strip())
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in _TRACKING_KEYS and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunparse(parts._replace(query=urlencode(kept), fragment="")).rstrip("/")


def decode_url(blob_url: str, *, interval: float) -> DecodeResult:
    try:
        result = gnewsdecoder(blob_url, interval=interval)
    except Exception as exc:  # noqa: BLE001 — any decoder error is a normal "broken" outcome
        return DecodeResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    if result.get("status") and result.get("decoded_url"):
        return DecodeResult(ok=True, url=normalize_url(str(result["decoded_url"])))
    return DecodeResult(ok=False, error=str(result.get("message", "unknown decoder failure")))


def _apply_result(
    article: Article, result: DecodeResult, report: DecodeReport, touched_topics: set[int]
) -> None:
    if result.ok and result.url:
        article.source_url = result.url
        article.status = ArticleStatus.decoded
        report.decoded += 1
        touched_topics.add(article.topic_id)
    else:
        article.status = ArticleStatus.broken
        report.broken += 1
        report.failures.append(f"[{article.id}] {result.error}")


def _dedupe_topic(session: Session, topic_id: int) -> int:
    rows = session.scalars(
        select(Article)
        .where(
            Article.topic_id == topic_id,
            Article.status != ArticleStatus.broken,
        )
        .order_by(Article.id)
    ).all()
    seen: set[str] = set()
    removed = 0
    for row in rows:
        if row.source_url in seen:
            session.delete(row)
            removed += 1
        else:
            seen.add(row.source_url)
    return removed


def run_decode(
    session: Session,
    *,
    limit: int | None = None,
    progress: ProgressCallback = noop,
    concurrency: int | None = None,
) -> DecodeReport:
    ing = load_settings().section("ingestion")
    interval = float(ing.get("decode_request_interval_sec", 1.0))
    workers = max(
        1, int(concurrency if concurrency is not None else ing.get("decode_concurrency", 1))
    )

    stmt = (
        select(Article)
        .where(
            Article.source_type == SourceType.google_news,
            Article.status == ArticleStatus.fetched,
        )
        .order_by(Article.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    articles = session.scalars(stmt).all()
    report = DecodeReport(considered=len(articles))
    touched_topics: set[int] = set()
    total = len(articles)

    if workers <= 1:
        for done, article in enumerate(articles, start=1):
            result = decode_url(article.source_url, interval=interval)
            _apply_result(article, result, report, touched_topics)
            session.commit()  # durable per article — a crash/interrupt keeps prior successes
            progress(done, total, article.source_name)
    else:
        # decode_url is pure HTTP + sleep, no DB access — safe to run off the main
        # thread. Every mutation (_apply_result, commit, progress) still happens
        # here, on the session's one owning thread.
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(decode_url, article.source_url, interval=interval): article
                for article in articles
            }
            for future in as_completed(futures):
                article = futures[future]
                try:
                    result = future.result()
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — a worker-thread failure is still "broken", not fatal
                    result = DecodeResult(ok=False, error=f"{type(exc).__name__}: {exc}")
                _apply_result(article, result, report, touched_topics)
                session.commit()
                done += 1
                progress(done, total, article.source_name)

    session.flush()
    for topic_id in touched_topics:
        report.deduped += _dedupe_topic(session, topic_id)

    return report

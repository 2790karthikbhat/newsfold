from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

import newsfold.process.extract as extract_mod
from newsfold.models import Article, ArticleStatus, SourceType, Topic, TopicMode, TopicStatus
from newsfold.process.extract import ExtractResult, extract_article, run_extract

_ARTICLE_HTML = (
    "<html><body><article><h1>Big story</h1><p>"
    + ("This is a real sentence with enough words to clear the minimum. " * 12)
    + "</p></article></body></html>"
).encode()

_STUB_HTML = b"<html><body><nav>menu</nav><p>Subscribe to read.</p></body></html>"


def _client(routes: dict[str, tuple[int, bytes]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(str(request.url), (404, b""))
        return httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_extract_article_returns_text() -> None:
    client = _client({"https://pub.example/story": (200, _ARTICLE_HTML)})
    result = extract_article("https://pub.example/story", client=client)
    assert result.ok
    assert "real sentence" in result.text


def test_extract_article_rejects_short_stub() -> None:
    client = _client({"https://pub.example/paywall": (200, _STUB_HTML)})
    result = extract_article("https://pub.example/paywall", client=client)
    assert not result.ok
    assert result.error is not None


def test_extract_article_handles_http_error() -> None:
    client = _client({})  # 404
    result = extract_article("https://pub.example/gone", client=client)
    assert not result.ok
    assert "fetch failed" in result.error


@pytest.fixture
def clustered_articles(db):
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    good = Article(
        topic_id=t.id,
        source_url="https://pub.example/good",
        source_name="Pub",
        source_type=SourceType.google_news,
        title_original="good",
        status=ArticleStatus.clustered,
        rank_score=5.0,
    )
    bad = Article(
        topic_id=t.id,
        source_url="https://pub.example/bad",
        source_name="Pub",
        source_type=SourceType.google_news,
        title_original="bad",
        status=ArticleStatus.clustered,
        rank_score=4.0,
    )
    skip = Article(
        topic_id=t.id,
        source_url="https://pub.example/skip",
        source_name="Pub",
        source_type=SourceType.google_news,
        title_original="not a survivor",
        status=ArticleStatus.decoded,
    )
    db.add_all([good, bad, skip])
    db.flush()
    return good, bad, skip


def test_run_extract_populates_body_and_flags_failures(db, clustered_articles, monkeypatch) -> None:
    good, bad, skip = clustered_articles

    def fake(url: str, *, timeout: float, client=None) -> ExtractResult:
        if url.endswith("/good"):
            return ExtractResult(ok=True, text="full body text")
        return ExtractResult(ok=False, error="paywall")

    monkeypatch.setattr(extract_mod, "extract_article", fake)

    report = run_extract(db)

    assert (report.considered, report.extracted, report.headline_only) == (2, 1, 1)
    assert good.body_original == "full body text"
    assert good.headline_only is False
    assert bad.body_original is None
    assert bad.headline_only is True
    assert skip.body_original is None and skip.headline_only is False  # never touched


def test_run_extract_is_idempotent(db, clustered_articles, monkeypatch) -> None:
    monkeypatch.setattr(
        extract_mod,
        "extract_article",
        lambda url, **k: ExtractResult(ok=True, text="body"),
    )
    first = run_extract(db)
    second = run_extract(db)

    assert first.considered == 2  # both clustered survivors
    assert second.considered == 0  # bodies filled -> nothing left to do
    assert (
        db.scalar(select(Article.body_original).where(Article.title_original == "good")) == "body"
    )


def test_run_extract_concurrent_matches_serial_outcome(db, monkeypatch) -> None:
    t = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add(t)
    db.flush()
    arts = [
        Article(
            topic_id=t.id,
            source_url=f"https://pub.example/{i}",
            source_name="Pub",
            source_type=SourceType.google_news,
            title_original=f"story {i}",
            status=ArticleStatus.clustered,
            rank_score=float(6 - i),
        )
        for i in range(6)
    ]
    db.add_all(arts)
    db.flush()

    def fake(url: str, *, timeout: float, client=None) -> ExtractResult:
        if url.endswith("/3"):
            return ExtractResult(ok=False, error="paywall")
        if url.endswith("/4"):
            raise RuntimeError("boom")  # simulates a worker-thread crash
        return ExtractResult(ok=True, text=f"body {url[-1]}")

    monkeypatch.setattr(extract_mod, "extract_article", fake)

    report = run_extract(db, concurrency=3)

    assert report.considered == 6
    assert report.extracted == 4
    assert report.headline_only == 2
    statuses = {
        a.id: db.scalar(select(Article.headline_only).where(Article.id == a.id)) for a in arts
    }
    assert statuses[arts[3].id] is True  # /3 -> paywall
    assert statuses[arts[4].id] is True  # /4 -> raised, still handled
    assert statuses[arts[0].id] is False

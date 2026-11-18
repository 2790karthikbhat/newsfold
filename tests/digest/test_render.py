from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import select

from newsfold.digest.render import (
    _excerpt,
    build_digest,
    render_html,
    render_markdown,
    write_digest,
)
from newsfold.models import (
    Article,
    ArticleStatus,
    ReadState,
    SourceType,
    Topic,
    TopicMode,
    TopicStatus,
)


def test_excerpt_strips_repeated_title_line_and_truncates() -> None:
    body = "My Headline\nFirst sentence here. Second sentence follows. Third one too."
    out = _excerpt(body, "My Headline", limit=30)
    assert out.startswith("First sentence here.")
    assert out.endswith("…")


def test_excerpt_none_without_body() -> None:
    assert _excerpt(None, "x") is None


@pytest.fixture
def digest_db(db):
    sport = Topic(
        keyword="sports Vietnam",
        mode=TopicMode.persistent,
        languages=json.dumps(["vi"]),
        status=TopicStatus.active,
    )
    empty = Topic(
        keyword="F1",
        mode=TopicMode.temporary,
        languages=json.dumps(["en"]),
        status=TopicStatus.active,
    )
    db.add_all([sport, empty])
    db.flush()

    db.add_all(
        [
            Article(
                topic_id=sport.id,
                source_url="https://vnexpress.net/a",
                source_name="VnExpress",
                source_type=SourceType.user_source,
                title_original="Việt Nam thắng",
                body_original="Việt Nam thắng\nĐội tuyển giành chiến thắng quan trọng tối qua.",
                language_original="vi",
                status=ArticleStatus.clustered,
                rank_score=5.0,
                cluster_id="c1",
                published_at=datetime(2026, 9, 8, 12, 0, 0),
            ),
            Article(
                topic_id=sport.id,
                source_url="https://tuoitre.vn/b",
                source_name="Tuoi Tre",
                source_type=SourceType.google_news,
                title_original="Tin phụ",
                status=ArticleStatus.clustered,
                rank_score=2.0,
                cluster_id="c2",
                headline_only=True,
            ),
            Article(  # not a survivor — must not appear
                topic_id=sport.id,
                source_url="https://x.com/c",
                source_name="X",
                source_type=SourceType.google_news,
                title_original="Chưa xếp hạng",
                status=ArticleStatus.decoded,
            ),
        ]
    )
    db.flush()
    return db


def test_build_digest_groups_orders_and_filters(digest_db) -> None:
    data = build_digest(digest_db)

    assert [t.keyword for t in data.topics] == ["sports Vietnam"]  # F1 has no survivors
    titles = [a.title for a in data.topics[0].articles]
    assert titles == ["Việt Nam thắng", "Tin phụ"]  # rank_score desc
    assert data.total_articles == 2

    first = data.topics[0].articles[0]
    assert first.excerpt and "Đội tuyển giành" in first.excerpt
    assert first.summary is None  # no LLM yet
    assert data.topics[0].articles[1].headline_only is True


def test_build_digest_excludes_read_and_archived_by_default(digest_db) -> None:
    survivors = [
        a for a in digest_db.scalars(select(Article)) if a.status == ArticleStatus.clustered
    ]
    survivors[0].read_state = ReadState.read
    survivors[1].read_state = ReadState.archived

    default = build_digest(digest_db)
    assert default.total_articles == 0  # both survivors are now read/archived

    full = build_digest(digest_db, include_read=True)
    assert full.total_articles == 2


def test_render_markdown_and_html_contain_content(digest_db) -> None:
    data = build_digest(digest_db)

    md = render_markdown(data)
    assert "## sports Vietnam" in md
    assert "### Việt Nam thắng" in md
    assert "(headline only)" in md

    doc = render_html(data)
    assert "<title>News Digest" in doc
    assert "Việt Nam thắng" in doc
    assert "headline only" in doc


def test_write_digest_writes_file(digest_db, tmp_path) -> None:
    path = write_digest(digest_db, fmt="md", out_dir=tmp_path)
    assert path.exists()
    assert path.suffix == ".md"
    assert path.read_text(encoding="utf-8").startswith("# News Digest")


def test_write_digest_rejects_bad_format(digest_db, tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown format"):
        write_digest(digest_db, fmt="pdf", out_dir=tmp_path)


def test_digest_footer_shows_latest_run_usage(digest_db) -> None:
    from newsfold.digest.render import build_digest, render_html
    from newsfold.llm.usage import UsageLog
    from newsfold.llm_usage import flush_usage
    from newsfold.models import Run

    assert build_digest(digest_db).usage_line is None
    run = Run()
    digest_db.add(run)
    digest_db.flush()
    log = UsageLog()
    log.record(
        stage="enrich",
        provider="ollama",
        model="llama3.1",
        prompt_tokens=1200,
        completion_tokens=300,
    )
    flush_usage(digest_db, log, run.id)
    digest_db.commit()

    data = build_digest(digest_db)
    assert data.usage_line == (
        f"run {run.id}: 1 LLM call(s), 1,500 tokens (1,200 in / 300 out), $0.0000"
    )
    assert data.usage_line in render_html(data)

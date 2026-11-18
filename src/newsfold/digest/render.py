"""Render the current run's surviving articles to a digest file.

Pure read + file write, no mutation. Groups by topic; within a topic, orders by
`llm_rank` (when the LLM ranking pass ran) then falls back to the cluster `rank_score`. Translated
title/body/summary are shown when present and degrade to the originals otherwise,
so this works before the LLM steps exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from newsfold.config import OUT_DIR, load_pricing, load_settings
from newsfold.llm.usage import summarize
from newsfold.llm_usage import latest_run_usage
from newsfold.models import Article, ArticleStatus, ReadState, Topic, TopicStatus
from newsfold.rank.score import registrable_domain
from newsfold.timeutil import utcnow

TEMPLATE_DIR = Path(__file__).parent / "templates"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class DigestArticle:
    id: int
    title: str
    title_original: str
    title_is_translated: bool
    summary: str | None
    excerpt: str | None
    body: str | None
    body_is_translated: bool
    source_name: str
    source_url: str
    published_display: str
    published_at: (
        datetime | None
    )  # additive: web/glance.py wants the raw value, not just the display string
    language_original: str | None
    headline_only: bool
    source_count: int
    rank_score: float | None
    llm_rank: int | None
    llm_reason: str | None
    read_state: str
    saved: bool


@dataclass
class DigestTopic:
    keyword: str
    articles: list[DigestArticle] = field(default_factory=list)


@dataclass
class DigestData:
    run_date: str
    generated_at: str
    target_language: str
    topics: list[DigestTopic] = field(default_factory=list)
    usage_line: str | None = None  # LLM tokens + cost of the latest run that used one

    @property
    def total_articles(self) -> int:
        return sum(len(t.articles) for t in self.topics)


def _excerpt(body: str | None, title_original: str, *, limit: int = 320) -> str | None:
    if not body:
        return None
    text = body.strip()
    # trafilatura prepends the <h1>; drop a leading line that just repeats the title
    first_nl = text.find("\n")
    if first_nl != -1 and text[:first_nl].strip().startswith(title_original[:40]):
        text = text[first_nl + 1 :].strip()
    if len(text) <= limit:
        return text
    sentences = _SENTENCE_END.split(text)
    out = ""
    for sentence in sentences:
        if out and len(out) + len(sentence) > limit:
            break
        out = f"{out} {sentence}".strip()
    return (out or text[:limit]).rstrip() + "…"


def _source_count(session: Session, cluster_id: str | None) -> int:
    if not cluster_id:
        return 1
    urls = session.scalars(select(Article.source_url).where(Article.cluster_id == cluster_id)).all()
    domains = {registrable_domain(u.split("/")[2]) for u in urls if "//" in u}
    return len(domains) or 1


def build_digest(session: Session, *, include_read: bool = False) -> DigestData:
    """`include_read=False` (default) only shows `unread` articles — once you mark
    something `read` or `archive` it, it stops reappearing in the next digest.
    `include_read=True` shows the full history regardless of read_state."""
    target_language = load_settings().target_language
    now = utcnow()
    data = DigestData(
        run_date=now.date().isoformat(),
        generated_at=now.strftime("%Y-%m-%d %H:%M UTC"),
        target_language=target_language,
    )

    topics = session.scalars(
        select(Topic).where(Topic.status == TopicStatus.active).order_by(Topic.id)
    ).all()

    for topic in topics:
        stmt = select(Article).where(
            Article.topic_id == topic.id,
            Article.status.in_((ArticleStatus.clustered, ArticleStatus.processed)),
        )
        if not include_read:
            stmt = stmt.where(Article.read_state == ReadState.unread)
        rows = session.scalars(
            stmt.order_by(
                Article.llm_rank.asc().nullslast(),
                Article.rank_score.desc().nullslast(),
                Article.id,
            )
        ).all()
        if not rows:
            continue

        dt = DigestTopic(keyword=topic.keyword)
        for a in rows:
            title = a.title_translated or a.title_original
            body = a.body_translated or a.body_original
            dt.articles.append(
                DigestArticle(
                    id=a.id,
                    title=title,
                    title_original=a.title_original,
                    title_is_translated=bool(a.title_translated)
                    and a.title_translated != a.title_original,
                    summary=a.summary,
                    excerpt=None if a.summary else _excerpt(a.body_original, a.title_original),
                    body=body,
                    body_is_translated=bool(a.body_translated),
                    source_name=a.source_name,
                    source_url=a.source_url,
                    published_display=(
                        a.published_at.strftime("%b %d, %Y") if a.published_at else "—"
                    ),
                    published_at=a.published_at,
                    language_original=a.language_original,
                    headline_only=a.headline_only,
                    source_count=_source_count(session, a.cluster_id),
                    rank_score=a.rank_score,
                    llm_rank=a.llm_rank,
                    llm_reason=a.llm_reason,
                    read_state=a.read_state.value,
                    saved=a.saved,
                )
            )
        data.topics.append(dt)

    data.usage_line = _usage_line(session)
    return data


def _usage_line(session: Session) -> str | None:
    found = latest_run_usage(session)
    if found is None:
        return None
    run_id, log = found
    s = summarize(log, load_pricing())
    tokens = s.totals.prompt_tokens + s.totals.completion_tokens
    return (
        f"run {run_id}: {s.totals.calls} LLM call(s), {tokens:,} tokens "
        f"({s.totals.prompt_tokens:,} in / {s.totals.completion_tokens:,} out), {s.cost_text()}"
    )


def render_html(data: DigestData) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("digest.html.j2").render(d=data)


def render_markdown(data: DigestData) -> str:
    lines = [
        f"# News Digest — {data.run_date}",
        "",
        f"_{data.total_articles} stories · generated {data.generated_at}_",
        "",
    ]
    for topic in data.topics:
        lines.append(f"## {topic.keyword}")
        lines.append("")
        for a in topic.articles:
            badge = " _(headline only)_" if a.headline_only else ""
            badge += " ★" if a.saved else ""
            badge += f" _({a.read_state})_" if a.read_state != "unread" else ""
            corrob = f" · {a.source_count} sources" if a.source_count > 1 else ""
            lines.append(f"### {a.title}{badge}")
            lines.append(
                f"{a.source_name} · {a.published_display}{corrob} · [original]({a.source_url})"
            )
            lines.append("")
            if a.summary:
                lines.append(a.summary)
            elif a.excerpt:
                lines.append(f"> {a.excerpt}")
            if a.llm_reason:
                lines.append(f"_{a.llm_reason}_")
            lines.append("")
    if data.usage_line:
        lines += ["---", f"_{data.usage_line}_", ""]
    return "\n".join(lines).rstrip() + "\n"


def write_digest(
    session: Session,
    *,
    fmt: str = "html",
    out_dir: Path | None = None,
    include_read: bool = False,
) -> Path:
    if fmt not in {"html", "md"}:
        raise ValueError(f"unknown format: {fmt!r}")
    data = build_digest(session, include_read=include_read)
    out_dir = out_dir or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"digest-{data.run_date}.{fmt}"
    content = render_html(data) if fmt == "html" else render_markdown(data)
    path.write_text(content, encoding="utf-8")
    return path

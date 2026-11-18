"""Typer entrypoint. Each command parses args, calls one stage function, prints a result.

No business logic here (CLAUDE.md layer rule).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import typer

from newsfold.db import session_scope
from newsfold.llm.usage import UsageLog
from newsfold.progress import stage_progress

app = typer.Typer(no_args_is_help=True, help="Newsfold pipeline.")
topics_app = typer.Typer(no_args_is_help=True, help="Manage topics.")
sources_app = typer.Typer(no_args_is_help=True, help="Manage user-added feeds.")
articles_app = typer.Typer(no_args_is_help=True, help="Inspect and curate stored articles.")
sports_app = typer.Typer(
    no_args_is_help=True, help="Fetch and browse followed sports fixtures/results."
)
app.add_typer(topics_app, name="topics")
app.add_typer(sources_app, name="sources")
app.add_typer(articles_app, name="articles")
app.add_typer(sports_app, name="sports")

DRY_RUN = typer.Option(False, "--dry-run", help="Print planned LLM calls; do not hit the API.")


@contextmanager
def _interruptible() -> Iterator[None]:
    """Each row is committed as it's processed, so Ctrl+C during a long stage keeps
    every success made so far — re-running only picks up what's left."""
    try:
        yield
    except KeyboardInterrupt:
        typer.echo("\ninterrupted — progress made so far is saved; re-run to continue")
        raise typer.Exit(130) from None


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing config files."),
) -> None:
    """Copy the default config into your config dir and create/upgrade the database."""
    from newsfold.config import CONFIG_DIR, HOME_DIR, OUT_DIR, init_config_dir, load_settings
    from newsfold.db import upgrade_db

    for path, action in init_config_dir(force=force):
        typer.echo(f"{action:<11} {path}")
    load_settings.cache_clear()
    url = upgrade_db()
    typer.echo(f"database    {url}")
    typer.echo(f"digests     {OUT_DIR}")
    typer.echo(
        f"\nnext: edit {CONFIG_DIR / 'topics.yaml'}, put API keys in {CONFIG_DIR / '.env'} "
        f"(see .env.example), then `digest topics sync && digest run`.\n"
        f"data home: {HOME_DIR} (override with NEWSFOLD_HOME)"
    )


@app.command()
def migrate() -> None:
    """Upgrade the database schema to the latest version (alembic upgrade head)."""
    from newsfold.db import upgrade_db

    typer.echo(f"upgraded {upgrade_db()}")


@app.command()
def fetch() -> None:
    """Google News RSS + active user feeds -> raw Article rows. No LLM, no decoding."""
    from newsfold.ingest.fetch import run_fetch

    with _interruptible(), session_scope() as session, stage_progress("Fetching feeds") as prog:
        report = run_fetch(session, progress=prog)

    header = f"{'topic':<30} {'channel':<16} {'found':>6} {'new':>5} {'stale':>6} {'dup':>5}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for s in report.stats:
        if s.error:
            typer.echo(f"{s.keyword:<30} {s.channel:<16}  ERROR  {s.error}")
            continue
        typer.echo(
            f"{s.keyword:<30} {s.channel:<16} {s.found:>6} {s.added:>5} "
            f"{s.stale:>6} {s.duplicate:>5}"
        )
    typer.echo("-" * len(header))
    typer.echo(f"run {report.run_id}: {report.total_added} new article(s) stored")


@app.command()
def decode(
    limit: int = typer.Option(0, "--limit", help="Decode at most N articles (0 = all)."),
    concurrency: int = typer.Option(
        0,
        "--concurrency",
        help="Concurrent decode workers (0 = use config's ingestion.decode_concurrency). "
        "Keep low — Google may start 429ing if pushed too hard.",
    ),
) -> None:
    """Resolve Google News redirect blobs to real publisher URLs."""
    from newsfold.ingest.decode import run_decode

    with _interruptible(), session_scope() as session, stage_progress("Decoding links") as prog:
        report = run_decode(
            session, limit=limit or None, progress=prog, concurrency=concurrency or None
        )

    typer.echo(
        f"considered {report.considered} · decoded {report.decoded} · "
        f"broken {report.broken} · deduped {report.deduped}"
    )
    for line in report.failures[:10]:
        typer.echo(f"  broken: {line}")


@app.command()
def cluster(
    show: int = typer.Option(12, "--show", help="Top clusters to print per topic."),
) -> None:
    """Group same-story articles and score them. No LLM."""
    from itertools import groupby

    from newsfold.rank.cluster import run_cluster

    with session_scope() as session:
        report = run_cluster(session)

    for keyword, group in groupby(report.summaries, key=lambda c: c.keyword):
        rows = sorted(group, key=lambda c: c.score, reverse=True)
        typer.echo(f"\n{keyword}")
        for c in rows[:show]:
            mark = "*" if c.survivor else " "
            t1 = "T1" if c.has_tier1 else "  "
            typer.echo(
                f" {mark} {c.score:5.2f}  {t1}  n={c.size:<2} src={c.distinct_sources:<2} "
                f"[{c.language}] {c.representative_title[:70]}"
            )
    typer.echo(
        f"\n{report.total_clusters} clusters across all topics · "
        f"{report.total_survivors} survivors (* = kept for ranking)"
    )


@app.command()
def status() -> None:
    """Show article counts by pipeline stage and the latest run summary."""
    from newsfold.views import pipeline_status

    with session_scope() as session:
        st = pipeline_status(session)

    typer.echo(f"articles: {st.total_articles}   clusters: {st.total_clusters}")
    typer.echo("by stage:      " + "  ".join(f"{k}={v}" for k, v in st.articles_by_status.items()))
    typer.echo(
        "by read state: "
        + "  ".join(f"{k}={v}" for k, v in st.articles_by_read_state.items())
        + f"  saved={st.articles_saved}"
    )
    typer.echo(
        "by source:     " + "  ".join(f"{k}={v}" for k, v in st.articles_by_source_type.items())
    )
    if st.latest_run:
        typer.echo(
            f"latest run #{st.latest_run['id']} "
            f"started {st.latest_run['started_at']} finished {st.latest_run['finished_at']}"
        )


@articles_app.command("list")
def articles_list(
    status: str = typer.Option(
        "", "--status", help="fetched|decoded|clustered|processed|discarded|broken"
    ),
    read_state: str = typer.Option("", "--read-state", help="unread|read|archived"),
    saved: bool = typer.Option(False, "--saved", help="Only saved (read-later) articles."),
    topic: str = typer.Option("", "--topic", help="Filter by topic keyword."),
    cluster: str = typer.Option("", "--cluster", help="Filter by cluster_id."),
    limit: int = typer.Option(30, "--limit"),
    urls: bool = typer.Option(False, "--urls", help="Print just id + status + source_url."),
) -> None:
    """Inspect stored articles and their resolved URLs (highest rank_score first)."""
    from newsfold.views import list_articles

    with session_scope() as session:
        rows = list_articles(
            session,
            status=status or None,
            read_state=read_state or None,
            saved=True if saved else None,
            topic_keyword=topic or None,
            cluster_id=cluster or None,
            limit=limit,
        )

    if not rows:
        typer.echo("no matching articles")
        return

    for r in rows:
        if urls:
            typer.echo(f"[{r.id:>5}] {r.status:<9} {r.source_url}")
            continue
        score = f"{r.rank_score:5.2f}" if r.rank_score is not None else "  -  "
        cid = (r.cluster_id or "-")[:10]
        mark = ("★" if r.saved else " ") + (
            "" if r.read_state == "unread" else f" [{r.read_state}]"
        )
        typer.echo(
            f"[{r.id:>5}] {r.status:<9} {score} {r.source_type:<11} cl={cid:<10} "
            f"{r.source_name[:22]:<22} {r.title[:55]}{mark}"
        )
        typer.echo(f"         {r.source_url}")
    typer.echo(f"\n{len(rows)} row(s)")


def _report_ids(verb: str, n: int, ids: list[int]) -> None:
    typer.echo(f"{verb} {n}/{len(ids)} article(s)")


@articles_app.command("read")
def articles_read(ids: list[int] = typer.Argument(..., help="Article IDs.")) -> None:
    """Mark articles read — they stop appearing in the default digest."""
    from newsfold.articles import mark_read

    with session_scope() as session:
        n = mark_read(session, ids)
    _report_ids("marked read", n, ids)


@articles_app.command("unread")
def articles_unread(ids: list[int] = typer.Argument(..., help="Article IDs.")) -> None:
    """Undo `read` — back to unread."""
    from newsfold.articles import mark_unread

    with session_scope() as session:
        n = mark_unread(session, ids)
    _report_ids("marked unread", n, ids)


@articles_app.command("archive")
def articles_archive(ids: list[int] = typer.Argument(..., help="Article IDs.")) -> None:
    """Archive articles — reversible, stops them appearing in the default digest."""
    from newsfold.articles import archive

    with session_scope() as session:
        n = archive(session, ids)
    _report_ids("archived", n, ids)


@articles_app.command("unarchive")
def articles_unarchive(ids: list[int] = typer.Argument(..., help="Article IDs.")) -> None:
    """Undo `archive` — back to read."""
    from newsfold.articles import unarchive

    with session_scope() as session:
        n = unarchive(session, ids)
    _report_ids("unarchived", n, ids)


@articles_app.command("save")
def articles_save(ids: list[int] = typer.Argument(..., help="Article IDs.")) -> None:
    """Save for later — independent of read/archived; never touched by `digest cleanup`."""
    from newsfold.articles import save

    with session_scope() as session:
        n = save(session, ids)
    _report_ids("saved", n, ids)


@articles_app.command("unsave")
def articles_unsave(ids: list[int] = typer.Argument(..., help="Article IDs.")) -> None:
    """Undo `save`."""
    from newsfold.articles import unsave

    with session_scope() as session:
        n = unsave(session, ids)
    _report_ids("unsaved", n, ids)


@articles_app.command("delete")
def articles_delete(
    ids: list[int] = typer.Argument(..., help="Article IDs."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Permanently delete articles — unlike archive, this is not reversible."""
    from newsfold.articles import delete

    if not yes and not typer.confirm(f"Permanently delete {len(ids)} article(s)?"):
        typer.echo("cancelled")
        raise typer.Exit(1)

    with session_scope() as session:
        n = delete(session, ids)
    _report_ids("deleted", n, ids)


@app.command()
def cleanup(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would happen; don't change anything."
    ),
    archive_days: int = typer.Option(
        0, "--archive-days", help="Override retention.auto_archive_after_days for this run."
    ),
    delete_days: int = typer.Option(
        0, "--delete-days", help="Override retention.auto_delete_after_days for this run."
    ),
) -> None:
    """Age-based housekeeping: auto-archive stale unread
    articles, hard-delete old discarded/broken rows. Never touches saved articles.
    Config-driven (config/settings.yaml [retention]) — run explicitly, e.g. from
    cron right after `digest run`; never runs on its own."""
    from newsfold.articles import run_cleanup

    with session_scope() as session:
        report = run_cleanup(
            session,
            dry_run=dry_run,
            archive_after_days=archive_days or None,
            delete_after_days=delete_days or None,
        )

    prefix = "[dry-run] would " if dry_run else ""
    typer.echo(
        f"{prefix}archive {report.archive_candidates} stale unread article(s)"
        + ("" if dry_run else f" -> archived {report.archived}")
    )
    typer.echo(
        f"{prefix}delete {report.delete_candidates} old discarded/broken article(s)"
        + ("" if dry_run else f" -> deleted {report.deleted}")
    )


@app.command()
def extract(
    limit: int = typer.Option(0, "--limit", help="Extract at most N articles (0 = all)."),
    concurrency: int = typer.Option(
        0,
        "--concurrency",
        help="Concurrent fetch workers (0 = use config's processing.extract_concurrency).",
    ),
) -> None:
    """Fetch full article text for ranked cluster survivors. No LLM."""
    from newsfold.process.extract import run_extract

    with (
        _interruptible(),
        session_scope() as session,
        stage_progress("Extracting articles") as prog,
    ):
        report = run_extract(
            session, limit=limit or None, progress=prog, concurrency=concurrency or None
        )

    typer.echo(
        f"considered {report.considered} · extracted {report.extracted} · "
        f"headline-only {report.headline_only}"
    )
    for line in report.failures[:10]:
        typer.echo(f"  headline-only: {line}")


def _echo_usage(log: UsageLog) -> None:
    from newsfold.config import load_pricing
    from newsfold.llm.usage import format_usage

    for line in format_usage(log, load_pricing()):
        typer.echo(line)


def _llm_guard(exc: Exception) -> None:
    typer.echo(f"error: {exc}")
    raise typer.Exit(1) from exc


@app.command()
def rank(dry_run: bool = DRY_RUN) -> None:
    """One newsworthiness LLM call per topic → top N."""
    from newsfold.llm.base import LLMConfigError
    from newsfold.rank.llm_rank import run_llm_rank

    try:
        with (
            _interruptible(),
            session_scope() as session,
            stage_progress("Ranking topics") as prog,
        ):
            report = run_llm_rank(session, dry_run=dry_run, progress=prog)
    except LLMConfigError as exc:
        _llm_guard(exc)
        return

    typer.echo(f"provider {report.provider}")
    _echo_usage(report.usage)
    for s in report.stats:
        if s.error:
            typer.echo(f"  {s.keyword:<30} ERROR {s.error}")
        elif s.skipped:
            typer.echo(f"  {s.keyword:<30} skipped ({s.candidates} candidates)")
        else:
            typer.echo(
                f"  {s.keyword:<30} {s.candidates} candidates → "
                f"{s.promoted} kept, {s.discarded} dropped"
            )


@app.command()
def process(
    dry_run: bool = DRY_RUN,
    limit: int = typer.Option(0, "--limit", help="Process at most N articles (0 = all / cap)."),
) -> None:
    """Classify + translate + summarize each survivor in one call."""
    from newsfold.llm.base import LLMConfigError
    from newsfold.process.enrich import run_enrich

    try:
        with (
            _interruptible(),
            session_scope() as session,
            stage_progress("Translating + summarizing") as prog,
        ):
            report = run_enrich(session, dry_run=dry_run, limit=limit or None, progress=prog)
    except LLMConfigError as exc:
        _llm_guard(exc)
        return

    if dry_run:
        typer.echo(f"[dry-run] {report.provider}: would send {report.planned} call(s)")
        return
    typer.echo(
        f"provider {report.provider} · considered {report.considered} · "
        f"enriched {report.enriched} · off-topic {report.discarded} · failed {report.failed}"
    )
    for line in report.failures[:10]:
        typer.echo(f"  failed: {line}")
    _echo_usage(report.usage)


@app.command()
def render(
    fmt: str = typer.Option("html", "--format", help="html | md"),
    open_file: bool = typer.Option(False, "--open", help="Open the file when done."),
    include_read: bool = typer.Option(
        False, "--include-read", help="Also show read/archived articles, not just unread."
    ),
) -> None:
    """Write the digest file for the current survivors. No LLM."""
    from newsfold.digest.render import write_digest

    with session_scope() as session:
        path = write_digest(session, fmt=fmt, include_read=include_read)

    typer.echo(f"wrote {path}")
    if open_file:
        typer.launch(str(path))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (local-only by default)."),
    port: int = typer.Option(8765, "--port", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
) -> None:
    """Start the local web UI for browsing digests and managing topics/sources."""
    from newsfold.web.app import run_server

    typer.echo(f"serving on http://{host}:{port} — Ctrl+C to stop")
    run_server(host=host, port=port, reload=reload)


@app.command()
def run(
    decode_limit: int = typer.Option(0, "--decode-limit", help="Cap decode calls (0 = all)."),
    decode_concurrency: int = typer.Option(
        0, "--decode-concurrency", help="Concurrent decode workers (0 = config default)."
    ),
    extract_limit: int = typer.Option(0, "--extract-limit", help="Cap extractions (0 = all)."),
    extract_concurrency: int = typer.Option(
        0, "--extract-concurrency", help="Concurrent extract workers (0 = config default)."
    ),
    fmt: str = typer.Option("html", "--format", help="Digest format: html | md"),
    no_render: bool = typer.Option(False, "--no-render", help="Skip the digest file."),
    include_read: bool = typer.Option(
        False, "--include-read", help="Also show read/archived articles in the digest."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Don't call the LLM; print planned calls."
    ),
) -> None:
    """fetch → decode → cluster → rank → extract → enrich → render, idempotently."""
    from newsfold.pipeline import run_pipeline

    with _interruptible():
        report = run_pipeline(
            decode_limit=decode_limit or None,
            decode_concurrency=decode_concurrency or None,
            extract_limit=extract_limit or None,
            extract_concurrency=extract_concurrency or None,
            render=not no_render,
            render_format=fmt,
            render_include_read=include_read,
            dry_run=dry_run,
            show_progress=True,
        )
    typer.echo(report.summary_line())
    for line in report.usage_lines():
        typer.echo(line)


@topics_app.command("sync")
def topics_sync() -> None:
    """Reconcile config/topics.yaml into the DB."""
    from newsfold.topics import sync_topics

    with session_scope() as session:
        report = sync_topics(session)

    for label, items in (
        ("added", report.added),
        ("updated", report.updated),
        ("unchanged", report.unchanged),
    ):
        if items:
            typer.echo(f"{label}: {', '.join(items)}")
    if report.in_db_not_in_config:
        typer.echo(
            "in DB but not in config (archive manually if stale): "
            + ", ".join(report.in_db_not_in_config)
        )


@topics_app.command("list")
def topics_list() -> None:
    """Show topics currently in the DB."""
    from sqlalchemy import select

    from newsfold.models import Topic

    with session_scope() as session:
        rows = session.scalars(select(Topic).order_by(Topic.id)).all()
        for t in rows:
            langs = ", ".join(json.loads(t.languages))
            typer.echo(f"[{t.id}] {t.keyword}  ({t.mode.value}, {t.status.value}, {langs})")
        if not rows:
            typer.echo("no topics — run `digest topics sync`")


@sources_app.command("add")
def sources_add(
    url: str,
    name: str = typer.Option("", "--name", help="Override the display / tier-weight name."),
    topic: str = typer.Option(
        "", "--topic", help="Scope this feed to one topic keyword (default: all topics)."
    ),
) -> None:
    """Discover the feed URL for a website and store it."""
    from newsfold.sources import SourceError, add_source

    with session_scope() as session:
        try:
            result = add_source(session, url, name=name or None, topic_keyword=topic or None)
        except SourceError as exc:
            typer.echo(f"error: {exc}")
            raise typer.Exit(1) from exc
        src = result.source
        verb = "added" if result.created else "updated"
        if src.status.value == "active":
            typer.echo(f"{verb} [{src.name}] -> {src.resolved_feed_url}")
        else:
            typer.echo(f"{verb} but BROKEN: {result.discovery.error}")
            raise typer.Exit(1)


@sources_app.command("list")
def sources_list() -> None:
    """Show user-added feeds."""
    from newsfold.sources import list_sources

    with session_scope() as session:
        rows = list_sources(session)
        for s in rows:
            scope = f"topic:{s.topic_id}" if s.topic_id else "all topics"
            typer.echo(
                f"[{s.id}] {s.name}  ({s.status.value}, {scope})\n"
                f"      in:  {s.input_url}\n"
                f"      feed: {s.resolved_feed_url or '—'}"
            )
        if not rows:
            typer.echo("no sources — add one with `digest sources add <url>`")


@sources_app.command("remove")
def sources_remove(source_id: int) -> None:
    """Delete a user-added feed by id."""
    from newsfold.sources import remove_source

    with session_scope() as session:
        ok = remove_source(session, source_id)
    typer.echo("removed" if ok else f"no source with id {source_id}")


@sports_app.command("fetch")
def sports_fetch(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Don't call any sports API; print an empty summary."
    ),
) -> None:
    """Refresh the sports cache from football-data.org, CricAPI, and Jolpica-F1.

    Explicit only — never runs inside `digest run`. Schedule this via cron (like
    `digest cleanup`) if you want it to stay fresh automatically."""
    from newsfold.sports.aggregate import refresh_all

    report = refresh_all(dry_run=dry_run)
    prefix = "[dry-run] " if dry_run else ""
    typer.echo(
        f"{prefix}football {report.football_count} · cricket {report.cricket_count} · "
        f"f1 {report.f1_count}"
    )
    for line in report.errors:
        typer.echo(f"  error: {line}")


@sports_app.command("list")
def sports_list() -> None:
    """Show the cached sports snapshot (run `digest sports fetch` first)."""
    from newsfold.sports.aggregate import load_cached

    snapshot = load_cached()
    if snapshot is None:
        typer.echo("no cached sports data — run `digest sports fetch`")
        return

    stale = " (stale — re-run `digest sports fetch`)" if snapshot.stale else ""
    typer.echo(f"fetched {snapshot.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}{stale}")
    for e in snapshot.events:
        when = e.start_time.strftime("%b %d %H:%M") if e.start_time else "—"
        line = f"[{e.sport:<8}] {when}  {e.title}"
        if e.result:
            line += f" — {e.result}"
        typer.echo(line)
    if not snapshot.events:
        typer.echo("no matching events")


@sources_app.command("sync")
def sources_sync() -> None:
    """Seed the sources table from config/sources.yaml."""
    from newsfold.sources import sync_sources

    with session_scope() as session:
        report = sync_sources(session)
        for r in report.results:
            state = r.source.status.value
            typer.echo(f"  {state:<7} {r.source.name}  ({r.source.input_url})")
    typer.echo(f"{len(report.results)} source(s) processed")


if __name__ == "__main__":
    app()

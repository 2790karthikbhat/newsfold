"""End-to-end orchestration for `digest run`.

Runs the stages in order, each in its own transaction so a crash mid-run keeps the
progress made so far (and every stage is idempotent, so a re-run resumes cleanly):

    fetch -> decode -> cluster -> rank -> extract -> enrich -> render

`rank` and `enrich` (the LLM stages) run only when `LLM_STAGES_ENABLED` is true and a
`[llm]` provider is configured; otherwise the run still produces a digest from the
originals + body excerpts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from newsfold.config import load_pricing
from newsfold.db import session_scope
from newsfold.digest.render import write_digest
from newsfold.ingest.decode import DecodeReport, run_decode
from newsfold.ingest.fetch import FetchReport, run_fetch
from newsfold.llm.base import LLMConfigError
from newsfold.llm.usage import UsageLog, format_usage, summarize
from newsfold.models import Run
from newsfold.process.enrich import EnrichReport, run_enrich
from newsfold.process.extract import ExtractReport, run_extract
from newsfold.progress import null_stage, stage_progress
from newsfold.rank.cluster import ClusterReport, run_cluster
from newsfold.rank.llm_rank import RankReport, run_llm_rank
from newsfold.timeutil import utcnow

LLM_STAGES_ENABLED = True


@dataclass
class RunReport:
    run_id: int | None = None
    fetch: FetchReport | None = None
    decode: DecodeReport | None = None
    cluster: ClusterReport | None = None
    rank: RankReport | None = None
    extract: ExtractReport | None = None
    enrich: EnrichReport | None = None
    digest_path: Path | None = None
    llm_skipped: str | None = None  # reason LLM stages did not run

    @property
    def usage(self) -> UsageLog:
        log = UsageLog()
        for stage in (self.rank, self.enrich):
            if stage is not None:
                log.merge(stage.usage)
        return log

    def usage_lines(self) -> list[str]:
        return format_usage(self.usage, load_pricing())

    def summary_line(self) -> str:
        parts: list[str] = []
        if self.fetch:
            parts.append(f"fetched {self.fetch.total_added} new")
        if self.decode:
            parts.append(f"decoded {self.decode.decoded} ({self.decode.broken} broken)")
        if self.cluster:
            parts.append(
                f"{self.cluster.total_clusters} clusters, {self.cluster.total_survivors} survivors"
            )
        if self.rank:
            parts.append(f"ranked {self.rank.total_promoted}")
        if self.extract:
            parts.append(
                f"extracted {self.extract.extracted} ({self.extract.headline_only} headline-only)"
            )
        if self.enrich:
            parts.append(
                f"enriched {self.enrich.enriched} "
                f"({self.enrich.discarded} off-topic, {self.enrich.failed} failed)"
            )
        if self.llm_skipped:
            parts.append(f"rank/enrich skipped ({self.llm_skipped})")
        if self.digest_path:
            parts.append(f"wrote {self.digest_path}")
        prefix = f"run {self.run_id}: " if self.run_id else "run: "
        return prefix + " · ".join(parts)

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "fetch": self.fetch.as_dict() if self.fetch else None,
            "decode": self.decode.as_dict() if self.decode else None,
            "cluster": (
                {"clusters": self.cluster.total_clusters, "survivors": self.cluster.total_survivors}
                if self.cluster
                else None
            ),
            "rank": (
                {"provider": self.rank.provider, "promoted": self.rank.total_promoted}
                if self.rank
                else None
            ),
            "extract": vars(self.extract) if self.extract else None,
            "enrich": (
                {k: v for k, v in vars(self.enrich).items() if k != "usage"}
                if self.enrich
                else None
            ),
            "llm_usage": self._usage_dict(),
            "llm_skipped": self.llm_skipped,
            "digest_path": str(self.digest_path) if self.digest_path else None,
        }

    def _usage_dict(self) -> dict[str, object] | None:
        log = self.usage
        if not log:
            return None
        s = summarize(log, load_pricing())
        return {
            "calls": s.totals.calls,
            "prompt_tokens": s.totals.prompt_tokens,
            "completion_tokens": s.totals.completion_tokens,
            "cost_usd_known": round(s.known_usd, 6),
            "cost_unknown_models": s.unknown_models,
        }


def run_pipeline(
    *,
    decode_limit: int | None = None,
    decode_concurrency: int | None = None,
    extract_limit: int | None = None,
    extract_concurrency: int | None = None,
    render: bool = True,
    render_format: str = "html",
    render_include_read: bool = False,
    dry_run: bool = False,
    show_progress: bool = False,
) -> RunReport:
    report = RunReport()
    run_llm = LLM_STAGES_ENABLED
    stage = stage_progress if show_progress else null_stage

    with session_scope() as session, stage("Fetching feeds") as prog:
        report.fetch = run_fetch(session, progress=prog)
        report.run_id = report.fetch.run_id

    with session_scope() as session, stage("Decoding links") as prog:
        report.decode = run_decode(
            session, limit=decode_limit, progress=prog, concurrency=decode_concurrency
        )

    with session_scope() as session:
        report.cluster = run_cluster(session)

    if run_llm:
        try:
            with session_scope() as session, stage("Ranking topics") as prog:
                report.rank = run_llm_rank(
                    session, dry_run=dry_run, progress=prog, run_id=report.run_id
                )
        except LLMConfigError as exc:
            run_llm = False
            report.llm_skipped = str(exc)

    with session_scope() as session, stage("Extracting articles") as prog:
        report.extract = run_extract(
            session, limit=extract_limit, progress=prog, concurrency=extract_concurrency
        )

    if run_llm:
        with session_scope() as session, stage("Translating + summarizing") as prog:
            report.enrich = run_enrich(
                session, dry_run=dry_run, progress=prog, run_id=report.run_id
            )

    if render:
        with session_scope() as session:
            report.digest_path = write_digest(
                session, fmt=render_format, include_read=render_include_read
            )

    with session_scope() as session:
        _finalize_run(session, report)

    return report


def _finalize_run(session: Session, report: RunReport) -> None:
    if report.run_id is None:
        return
    run = session.get(Run, report.run_id)
    if run is None:
        return
    run.finished_at = utcnow()
    run.stats = json.dumps(report.as_dict())

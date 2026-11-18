from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import newsfold.pipeline as pipeline
from newsfold.ingest.decode import DecodeReport
from newsfold.ingest.fetch import ChannelStat, FetchReport
from newsfold.process.enrich import EnrichReport
from newsfold.process.extract import ExtractReport
from newsfold.rank.cluster import ClusterReport, TopicClusterStat
from newsfold.rank.llm_rank import RankReport, TopicRankStat


@contextmanager
def _scope(session):
    yield session


def _fetch_report() -> FetchReport:
    r = FetchReport(run_id=7)
    s = ChannelStat(keyword="F1", channel="en")
    s.added = 5
    r.stats.append(s)
    return r


def _cluster_report() -> ClusterReport:
    r = ClusterReport()
    r.stats.append(TopicClusterStat(keyword="F1", language="en", articles=10, clusters=4))
    return r


def _rank_report() -> RankReport:
    r = RankReport(provider="fake:model")
    stat = TopicRankStat(keyword="F1", candidates=4, promoted=2, discarded=2)
    r.stats.append(stat)
    return r


def _patch_stages(monkeypatch, *, order: list[str], llm: bool = True) -> None:
    monkeypatch.setattr(pipeline, "LLM_STAGES_ENABLED", llm)
    monkeypatch.setattr(pipeline, "session_scope", lambda: _scope(object()))
    monkeypatch.setattr(
        pipeline, "run_fetch", lambda s, progress=None: (order.append("fetch"), _fetch_report())[1]
    )
    monkeypatch.setattr(
        pipeline,
        "run_decode",
        lambda s, limit=None, progress=None, concurrency=None: (
            order.append(f"decode:{limit}"),
            DecodeReport(decoded=3, broken=1),
        )[1],
    )
    monkeypatch.setattr(
        pipeline, "run_cluster", lambda s: (order.append("cluster"), _cluster_report())[1]
    )
    monkeypatch.setattr(
        pipeline,
        "run_llm_rank",
        lambda s, dry_run=False, progress=None, run_id=None: (order.append("rank"), _rank_report())[
            1
        ],
    )
    monkeypatch.setattr(
        pipeline,
        "run_extract",
        lambda s, limit=None, progress=None, concurrency=None: (
            order.append(f"extract:{limit}"),
            ExtractReport(considered=2, extracted=2),
        )[1],
    )
    monkeypatch.setattr(
        pipeline,
        "run_enrich",
        lambda s, dry_run=False, progress=None, run_id=None: (
            order.append("enrich"),
            EnrichReport(provider="fake", considered=2, enriched=2),
        )[1],
    )
    monkeypatch.setattr(
        pipeline,
        "write_digest",
        lambda s, fmt="html", include_read=False: (
            order.append(f"render:{fmt}"),
            Path(f"out/digest.{fmt}"),
        )[1],
    )
    monkeypatch.setattr(pipeline, "_finalize_run", lambda s, r: order.append("finalize"))


def test_run_pipeline_runs_stages_in_order(monkeypatch) -> None:
    order: list[str] = []
    _patch_stages(monkeypatch, order=order)

    report = pipeline.run_pipeline(decode_limit=50, extract_limit=None)

    assert order == [
        "fetch",
        "decode:50",
        "cluster",
        "rank",
        "extract:None",
        "enrich",
        "render:html",
        "finalize",
    ]
    assert report.run_id == 7
    assert report.digest_path == Path("out/digest.html")


def test_run_pipeline_without_llm(monkeypatch) -> None:
    order: list[str] = []
    _patch_stages(monkeypatch, order=order, llm=False)

    pipeline.run_pipeline()

    assert "rank" not in order and "enrich" not in order
    assert order == ["fetch", "decode:None", "cluster", "extract:None", "render:html", "finalize"]


def test_run_pipeline_no_render(monkeypatch) -> None:
    order: list[str] = []
    _patch_stages(monkeypatch, order=order)

    report = pipeline.run_pipeline(render=False)

    assert "render:html" not in order
    assert report.digest_path is None


def test_summary_line_mentions_each_stage(monkeypatch) -> None:
    _patch_stages(monkeypatch, order=[])
    report = pipeline.run_pipeline()

    line = report.summary_line()
    assert line.startswith("run 7:")
    assert "fetched 5 new" in line
    assert "decoded 3 (1 broken)" in line
    assert "ranked 2" in line
    assert "extracted 2 (0 headline-only)" in line
    assert "enriched 2 (0 off-topic, 0 failed)" in line
    assert "wrote out/digest.html" in line

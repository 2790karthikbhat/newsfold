from __future__ import annotations

import pytest

import newsfold.llm.client as client_mod
from newsfold.config import ModelPrice
from newsfold.llm.base import LLMResult, Provider
from newsfold.llm.client import complete_json
from newsfold.llm.usage import (
    UsageKey,
    UsageLog,
    UsageTotals,
    cost_usd,
    find_price,
    format_usage,
    summarize,
)
from newsfold.llm_usage import flush_usage, latest_run_usage, usage_for_run
from newsfold.models import LLMUsage, Run

PRICING = {
    "anthropic": {"claude-haiku-4-5": ModelPrice(input=1.0, output=5.0)},
    "openai": {"gpt-4o-mini": ModelPrice(input=0.15, output=0.60)},
}


def _log(*records: tuple[str, str, str, int | None, int | None]) -> UsageLog:
    log = UsageLog()
    for stage, provider, model, p, c in records:
        log.record(
            stage=stage, provider=provider, model=model, prompt_tokens=p, completion_tokens=c
        )
    return log


def test_record_aggregates_per_stage_provider_model() -> None:
    log = _log(
        ("enrich", "anthropic", "claude-haiku-4-5", 100, 20),
        ("enrich", "anthropic", "claude-haiku-4-5", 50, 10),
        ("rank", "anthropic", "claude-haiku-4-5", 300, 40),
    )
    enrich = log.totals[UsageKey("enrich", "anthropic", "claude-haiku-4-5")]
    assert (enrich.calls, enrich.prompt_tokens, enrich.completion_tokens) == (2, 150, 30)
    total = log.grand_total()
    assert (total.calls, total.prompt_tokens, total.completion_tokens) == (3, 450, 70)


def test_missing_usage_counts_call_but_no_tokens() -> None:
    log = _log(("enrich", "ollama", "llama3.1", None, None), ("enrich", "ollama", "llama3.1", 5, 1))
    t = log.totals[UsageKey("enrich", "ollama", "llama3.1")]
    assert (t.calls, t.calls_without_usage, t.prompt_tokens) == (2, 1, 5)


def test_take_pending_returns_only_new_deltas() -> None:
    log = _log(("enrich", "openai", "gpt-4o-mini", 10, 1))
    assert sum(t.calls for t in log.take_pending().values()) == 1
    assert log.take_pending() == {}
    log.record(
        stage="enrich", provider="openai", model="gpt-4o-mini", prompt_tokens=1, completion_tokens=1
    )
    assert sum(t.calls for t in log.take_pending().values()) == 1
    assert log.grand_total().calls == 2


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("anthropic", "claude-haiku-4-5", 1.0),
        ("anthropic", "claude-haiku-4-5-20251001", 1.0),  # dated snapshot
        ("openai", "gpt-4o-mini-2024-07-18", 0.15),
        ("anthropic", "claude-haiku-4-5-turbo", None),  # not a date suffix → no match
        ("azure", "my-deployment", None),
    ],
)
def test_find_price(provider: str, model: str, expected: float | None) -> None:
    price = find_price(PRICING, provider, model)
    assert (price.input if price else None) == expected


def test_cost_math() -> None:
    totals = UsageTotals(calls=3, prompt_tokens=1_000_000, completion_tokens=200_000)
    key = UsageKey("enrich", "anthropic", "claude-haiku-4-5-20251001")
    assert cost_usd(key, totals, PRICING) == pytest.approx(1.0 + 1.0)  # $1 in + 0.2M x $5
    assert cost_usd(UsageKey("enrich", "lmstudio", "anything"), totals, PRICING) == 0.0
    assert cost_usd(UsageKey("enrich", "openai", "unknown-model"), totals, PRICING) is None


def test_summarize_and_format_mix_known_and_unknown() -> None:
    log = _log(
        ("enrich", "openai", "gpt-4o-mini", 2_000_000, 1_000_000),  # 0.30 + 0.60
        ("rank", "openai", "mystery", 10, 10),
    )
    s = summarize(log, PRICING)
    assert s.known_usd == pytest.approx(0.90)
    assert s.unknown_models == ["openai:mystery"]
    assert "cost unknown for openai:mystery" in s.cost_text()
    lines = format_usage(log, PRICING)
    assert lines[0] == "LLM usage:"
    assert any("enrich" in line and "$0.9000" in line for line in lines)
    assert any("rank" in line and "cost unknown" in line for line in lines)
    assert lines[-1].strip().startswith("total   2 call(s)")
    assert format_usage(UsageLog(), PRICING) == []


def test_only_unknown_reads_cost_unknown() -> None:
    s = summarize(_log(("rank", "azure", "dep", 1, 1)), PRICING)
    assert s.cost_text() == "cost unknown (azure:dep not in pricing.yaml)"


def test_flush_upserts_per_run_and_inserts_for_standalone(db) -> None:
    run = Run()
    db.add(run)
    db.flush()
    log = _log(("enrich", "openai", "gpt-4o-mini", 10, 2))
    flush_usage(db, log, run.id)
    log.record(
        stage="enrich", provider="openai", model="gpt-4o-mini", prompt_tokens=5, completion_tokens=1
    )
    log.record(
        stage="rank", provider="openai", model="gpt-4o-mini", prompt_tokens=7, completion_tokens=3
    )
    flush_usage(db, log, run.id)
    flush_usage(db, _log(("enrich", "openai", "gpt-4o-mini", 1, 1)), None)
    db.commit()

    rows = db.query(LLMUsage).filter(LLMUsage.run_id == run.id).all()
    assert len(rows) == 2  # enrich row was updated in place, not duplicated
    rebuilt = usage_for_run(db, run.id)
    enrich = rebuilt.totals[UsageKey("enrich", "openai", "gpt-4o-mini")]
    assert (enrich.calls, enrich.prompt_tokens, enrich.completion_tokens) == (2, 15, 3)
    assert db.query(LLMUsage).filter(LLMUsage.run_id.is_(None)).count() == 1

    found = latest_run_usage(db)
    assert found is not None and found[0] == run.id
    assert found[1].grand_total().calls == 3


class _Replies(Provider):
    name = "fake"

    def __init__(self, replies: list[LLMResult]) -> None:
        self._replies = replies
        self.calls = 0

    def complete(self, **kwargs) -> LLMResult:
        r = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return r


class _Settings:
    def llm(self) -> dict:
        return {"provider": "ollama", "ollama": {"model": "llama3.1"}}


def test_complete_json_records_every_call_including_retry(monkeypatch) -> None:
    provider = _Replies(
        [
            LLMResult(text="not json", model="m", prompt_tokens=10, completion_tokens=2),
            LLMResult(text='{"ok": 1}', model="m"),  # no usage reported
        ]
    )
    monkeypatch.setattr(client_mod, "load_settings", lambda: _Settings())
    monkeypatch.setattr(client_mod, "_build_provider", lambda cfg: provider)
    log = UsageLog()
    assert complete_json(system="s", user="u", task="rank", usage=log) == {"ok": 1}
    t = log.totals[UsageKey("rank", "ollama", "llama3.1")]
    assert (t.calls, t.prompt_tokens, t.calls_without_usage) == (2, 10, 1)


def test_complete_json_dry_run_records_nothing(monkeypatch) -> None:
    monkeypatch.setattr(client_mod, "load_settings", lambda: _Settings())
    log = UsageLog()
    complete_json(system="s", user="u", dry_run=True, usage=log)
    assert not log


def test_standalone_flushes_update_one_row_per_key(db) -> None:
    log = UsageLog()
    for _ in range(3):
        log.record(
            stage="enrich",
            provider="openai",
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=1,
        )
        flush_usage(db, log, None)
    db.commit()
    rows = db.query(LLMUsage).filter(LLMUsage.run_id.is_(None)).all()
    assert [(r.calls, r.prompt_tokens) for r in rows] == [(3, 30)]

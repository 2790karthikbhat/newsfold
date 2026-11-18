"""Token accounting + cost math for LLM calls. Pure — no DB, no network.

`llm.client.complete_json(usage=...)` records every provider call (retries
included) into a `UsageLog`; stages flush it to the `llm_usage` table
(`newsfold.llm_usage`) at their per-item commits. Prices come from
`config/pricing.yaml` via `config.load_pricing()`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from newsfold.config import ModelPrice

LOCAL_PROVIDERS = frozenset({"lmstudio", "ollama"})
_SNAPSHOT_SUFFIX = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")

Pricing = Mapping[str, Mapping[str, ModelPrice]]


@dataclass(frozen=True, order=True)
class UsageKey:
    stage: str
    provider: str
    model: str


@dataclass
class UsageTotals:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls_without_usage: int = 0  # backend returned no `usage` block (some local servers)

    def add(self, other: UsageTotals) -> None:
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.calls_without_usage += other.calls_without_usage


class UsageLog:
    """Running per-(stage, provider, model) totals, plus the part not yet persisted."""

    def __init__(self) -> None:
        self.totals: dict[UsageKey, UsageTotals] = {}
        self._pending: dict[UsageKey, UsageTotals] = {}
        # DB row id per key once persisted — lets repeated flushes update one row even
        # when there's no run id to look it up by (standalone `digest process`).
        self.row_ids: dict[UsageKey, int] = {}

    def record(
        self,
        *,
        stage: str,
        provider: str,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        delta = UsageTotals(
            calls=1,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            calls_without_usage=int(prompt_tokens is None and completion_tokens is None),
        )
        key = UsageKey(stage, provider, model)
        for bucket in (self.totals, self._pending):
            bucket.setdefault(key, UsageTotals()).add(delta)

    def add_totals(self, key: UsageKey, totals: UsageTotals) -> None:
        """Merge already-persisted totals (e.g. rebuilt from the DB) — not pending."""
        self.totals.setdefault(key, UsageTotals()).add(totals)

    def take_pending(self) -> dict[UsageKey, UsageTotals]:
        pending, self._pending = self._pending, {}
        return pending

    def merge(self, other: UsageLog) -> None:
        for key, totals in other.totals.items():
            self.add_totals(key, totals)

    def grand_total(self) -> UsageTotals:
        out = UsageTotals()
        for totals in self.totals.values():
            out.add(totals)
        return out

    def __bool__(self) -> bool:
        return bool(self.totals)


def find_price(pricing: Pricing, provider: str, model: str) -> ModelPrice | None:
    table = pricing.get(provider.lower()) or {}
    if model in table:
        return table[model]
    base = _SNAPSHOT_SUFFIX.sub("", model)
    return table.get(base) if base != model else None


def cost_usd(key: UsageKey, totals: UsageTotals, pricing: Pricing) -> float | None:
    """USD for these totals; 0.0 for local providers; None when the model has no price."""
    if key.provider in LOCAL_PROVIDERS:
        return 0.0
    price = find_price(pricing, key.provider, key.model)
    if price is None:
        return None
    return (totals.prompt_tokens * price.input + totals.completion_tokens * price.output) / 1e6


@dataclass
class CostSummary:
    totals: UsageTotals
    known_usd: float
    unknown_models: list[str]

    def cost_text(self) -> str:
        if not self.unknown_models:
            return f"${self.known_usd:.4f}"
        unknown = ", ".join(self.unknown_models)
        if self.known_usd:
            return f"${self.known_usd:.4f} + cost unknown for {unknown}"
        return f"cost unknown ({unknown} not in pricing.yaml)"


def summarize(log: UsageLog, pricing: Pricing) -> CostSummary:
    known = 0.0
    unknown: list[str] = []
    for key, totals in log.totals.items():
        cost = cost_usd(key, totals, pricing)
        if cost is None:
            label = f"{key.provider}:{key.model}"
            if label not in unknown:
                unknown.append(label)
        else:
            known += cost
    return CostSummary(totals=log.grand_total(), known_usd=known, unknown_models=unknown)


def _tok(n: int) -> str:
    return f"{n:,}"


def format_usage(log: UsageLog, pricing: Pricing) -> list[str]:
    """Human-readable per-stage lines plus a total line; [] when nothing was called."""
    if not log:
        return []
    lines = ["LLM usage:"]
    for key in sorted(log.totals):
        t = log.totals[key]
        cost = cost_usd(key, t, pricing)
        cost_s = f"${cost:.4f}" if cost is not None else "cost unknown"
        missing = f" ({t.calls_without_usage} without usage data)" if t.calls_without_usage else ""
        lines.append(
            f"  {key.stage:<7} {key.provider}:{key.model}  {t.calls} call(s)  "
            f"{_tok(t.prompt_tokens)} in / {_tok(t.completion_tokens)} out  {cost_s}{missing}"
        )
    s = summarize(log, pricing)
    lines.append(
        f"  total   {s.totals.calls} call(s)  {_tok(s.totals.prompt_tokens)} in / "
        f"{_tok(s.totals.completion_tokens)} out  {s.cost_text()}"
    )
    return lines

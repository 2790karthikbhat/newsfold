"""Provider-agnostic LLM contract.

A `Provider` takes a system + user prompt and returns raw text. JSON parsing,
schema validation, retries and dry-run live one level up in `llm.client`, so every
provider stays a thin HTTP wrapper.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMError(RuntimeError):
    """A provider call failed (transport, HTTP status, or empty response)."""


class LLMConfigError(RuntimeError):
    """The `[llm]` config is missing, unknown, or lacks a required field."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class Provider(ABC):
    name: str

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LLMResult:
        """Single-turn completion. `json_mode` asks the backend for a JSON object
        when it supports one; callers must still tolerate prose-wrapped JSON."""

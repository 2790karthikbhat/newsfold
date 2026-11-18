"""Anthropic Messages API provider (raw HTTP — no SDK dependency).

Anthropic has no JSON-object response mode, so `json_mode` is a no-op here; the
prompt asks for JSON and `llm.client` extracts it from the reply.
"""

from __future__ import annotations

from typing import Any

import httpx

from newsfold.llm.base import LLMError, LLMResult, Provider

_API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        self._client = client or httpx.Client(timeout=timeout)

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,  # noqa: ARG002 — Anthropic has no JSON mode; the prompt handles it
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            resp = self._client.post(
                f"{self._base_url}/v1/messages", json=payload, headers=self._headers
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"anthropic HTTP {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic request failed: {exc}") from exc

        try:
            text = "".join(
                block["text"] for block in body["content"] if block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise LLMError("anthropic returned an unexpected response shape") from exc
        if not text:
            raise LLMError("anthropic returned empty content")

        usage = body.get("usage") or {}
        return LLMResult(
            text=text,
            model=body.get("model", model),
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
        )

"""OpenAI-compatible chat-completions provider — covers OpenAI, Azure OpenAI, LM Studio
and Ollama.

All of them expose `POST {base_url}/chat/completions` with the same request/response
shape. Local backends (LM Studio, Ollama) ignore the API key; OpenAI requires it as a
Bearer token. Azure differs only in the URL (a per-deployment path plus an
`api-version` query, or the `/openai/v1` form) and in sending the key as `api-key`
— both passed in by `llm.client`, so there's still one code path.
"""

from __future__ import annotations

from typing import Any

import httpx

from newsfold.llm.base import LLMError, LLMResult, Provider


class OpenAICompatProvider(Provider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None,
        timeout: float,
        client: httpx.Client | None = None,
        api_key_header: str = "Authorization",
        query_params: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._params = dict(query_params or {})
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers[api_key_header] = (
                f"Bearer {api_key}" if api_key_header == "Authorization" else api_key
            )
        self._client = client or httpx.Client(timeout=timeout)
        self._last_error: LLMError | None = None

    def _post(self, payload: dict[str, Any], *, retryable_400: bool) -> dict[str, Any] | None:
        """Return the parsed body, or None if this was a retryable `response_format` 400."""
        try:
            resp = self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers,
                params=self._params or None,
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            return body
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            self._last_error = LLMError(f"{self.name} HTTP {exc.response.status_code}: {detail}")
            if retryable_400 and exc.response.status_code == 400 and "response_format" in detail:
                return None
            raise self._last_error from exc
        except httpx.HTTPError as exc:
            self._last_error = LLMError(f"{self.name} request failed: {exc}")
            raise self._last_error from exc

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
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        body = self._post(payload, retryable_400=json_mode)
        if body is None:
            # backend rejected `response_format` (e.g. LM Studio + MLX) — drop it and
            # rely on the prompt's JSON instruction.
            payload.pop("response_format", None)
            body = self._post(payload, retryable_400=False)
        if body is None:  # pragma: no cover - _post(retryable_400=False) never returns None
            raise self._last_error or LLMError(f"{self.name} request failed")

        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name} returned an unexpected response shape") from exc
        if not text:
            raise LLMError(f"{self.name} returned empty content")

        usage = body.get("usage") or {}
        return LLMResult(
            text=text,
            model=body.get("model", model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

from __future__ import annotations

import httpx
import pytest

from newsfold.llm.anthropic_provider import AnthropicProvider
from newsfold.llm.base import LLMError
from newsfold.llm.openai_compat import OpenAICompatProvider


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openai_compat_happy_path() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "model": "llama3.1",
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    p = OpenAICompatProvider(
        name="ollama", base_url="http://x/v1", api_key=None, timeout=5, client=_client(handler)
    )
    result = p.complete(
        system="s", user="u", model="llama3.1", max_tokens=100, temperature=0.0, json_mode=True
    )
    assert result.text == '{"ok": true}'
    assert result.prompt_tokens == 10
    assert captured["url"].endswith("/chat/completions")
    assert '"response_format"' in captured["body"]


def test_openai_compat_retries_without_response_format_on_400() -> None:
    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        has_rf = '"response_format"' in request.read().decode()
        seen.append(has_rf)
        if has_rf:
            return httpx.Response(
                400, json={"error": "'response_format.type' must be 'json_schema' or 'text'"}
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})

    p = OpenAICompatProvider(
        name="lmstudio", base_url="http://x/v1", api_key=None, timeout=5, client=_client(handler)
    )
    result = p.complete(
        system="s", user="u", model="m", max_tokens=10, temperature=0.0, json_mode=True
    )
    assert result.text == '{"ok": 1}'
    assert seen == [True, False]  # first with response_format, retry without


def test_openai_compat_raises_on_http_error() -> None:
    p = OpenAICompatProvider(
        name="openai",
        base_url="http://x/v1",
        api_key="k",
        timeout=5,
        client=_client(lambda r: httpx.Response(429, text="rate limited")),
    )
    with pytest.raises(LLMError, match="HTTP 429"):
        p.complete(system="s", user="u", model="m", max_tokens=10, temperature=0.0, json_mode=False)


def test_anthropic_concatenates_text_blocks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "sk-1"
        assert request.headers["anthropic-version"]
        return httpx.Response(
            200,
            json={
                "model": "claude-haiku",
                "content": [
                    {"type": "text", "text": '{"a":'},
                    {"type": "text", "text": " 1}"},
                ],
                "usage": {"input_tokens": 20, "output_tokens": 5},
            },
        )

    p = AnthropicProvider(
        base_url="https://api.anthropic.com", api_key="sk-1", timeout=5, client=_client(handler)
    )
    result = p.complete(
        system="s", user="u", model="claude-haiku", max_tokens=100, temperature=0.0, json_mode=True
    )
    assert result.text == '{"a": 1}'
    assert result.completion_tokens == 5

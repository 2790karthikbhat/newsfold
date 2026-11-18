from __future__ import annotations

import httpx
import pytest

import newsfold.llm.client as client_mod
from newsfold.llm.base import LLMConfigError
from newsfold.llm.client import (
    AZURE_DEFAULT_API_VERSION,
    LLMConfig,
    _build_provider,
    azure_url,
    complete_json,
)
from newsfold.llm.openai_compat import OpenAICompatProvider

_EP = "https://myres.openai.azure.com"


def _raw(**block: str) -> dict:
    return {
        "provider": "azure",
        "azure": {"endpoint": _EP, "deployment": "gpt4o-dep", "api_key_env": "AZ_KEY", **block},
    }


def test_azure_url_dated_version() -> None:
    assert azure_url(_EP + "/", "dep", "2024-10-21") == (
        f"{_EP}/openai/deployments/dep",
        {"api-version": "2024-10-21"},
    )


@pytest.mark.parametrize(
    ("endpoint", "version"), [(_EP, "v1"), (_EP + "/openai/v1/", "2024-10-21")]
)
def test_azure_url_v1_forms(endpoint: str, version: str) -> None:
    assert azure_url(endpoint, "dep", version) == (f"{_EP}/openai/v1", {})


def test_llmconfig_azure_deployment_alias_and_defaults(monkeypatch) -> None:
    monkeypatch.setenv("AZ_KEY", "k")
    cfg = LLMConfig(_raw(rank_deployment="rank-dep"), "enrich")
    assert cfg.model == "gpt4o-dep"
    assert cfg.api_version == AZURE_DEFAULT_API_VERSION
    assert cfg.needs_key is True
    assert LLMConfig(_raw(rank_deployment="rank-dep"), "rank").model == "rank-dep"


def test_build_provider_requires_endpoint_and_key(monkeypatch) -> None:
    monkeypatch.setenv("AZ_KEY", "k")
    with pytest.raises(LLMConfigError, match="endpoint"):
        _build_provider(LLMConfig(_raw(endpoint=""), "enrich"))
    monkeypatch.delenv("AZ_KEY")
    with pytest.raises(LLMConfigError, match="needs AZ_KEY"):
        _build_provider(LLMConfig(_raw(), "enrich"))


def _capture(captured: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-2024-08-06",
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            },
        )

    return handler


@pytest.mark.parametrize(
    ("version", "expected_url"),
    [
        (
            "2024-10-21",
            f"{_EP}/openai/deployments/gpt4o-dep/chat/completions?api-version=2024-10-21",
        ),
        ("v1", f"{_EP}/openai/v1/chat/completions"),
    ],
)
def test_azure_request_shape(monkeypatch, version: str, expected_url: str) -> None:
    monkeypatch.setenv("AZ_KEY", "secret-key")
    provider = _build_provider(LLMConfig(_raw(api_version=version), "enrich"))
    assert isinstance(provider, OpenAICompatProvider)
    captured: dict = {}
    provider._client = httpx.Client(transport=httpx.MockTransport(_capture(captured)))

    result = provider.complete(
        system="s", user="u", model="gpt4o-dep", max_tokens=50, temperature=0.0, json_mode=True
    )

    assert captured["url"] == expected_url
    assert captured["headers"]["api-key"] == "secret-key"
    assert "authorization" not in captured["headers"]
    assert '"model":"gpt4o-dep"' in captured["body"].replace(" ", "")
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 4


def test_azure_dry_run_needs_no_key_or_network(monkeypatch, capsys) -> None:
    monkeypatch.delenv("AZ_KEY", raising=False)

    class _S:
        def llm(self) -> dict:
            return _raw()

    monkeypatch.setattr(client_mod, "load_settings", lambda: _S())
    monkeypatch.setattr(client_mod, "_build_provider", lambda cfg: pytest.fail("no provider"))
    assert complete_json(system="s", user="u", dry_run=True) == {}
    assert "azure:gpt4o-dep" in capsys.readouterr().out

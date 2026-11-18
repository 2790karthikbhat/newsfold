from __future__ import annotations

import pytest

import newsfold.llm.client as client_mod
from newsfold.llm.base import LLMConfigError, LLMError, LLMResult, Provider
from newsfold.llm.client import LLMConfig, _extract_json, complete_json

_ANTHROPIC = {
    "provider": "anthropic",
    "anthropic": {"model": "haiku", "rank_model": "sonnet", "api_key_env": "TEST_KEY"},
}
_OLLAMA = {"provider": "ollama", "ollama": {"model": "llama3.1"}}


def test_llmconfig_anthropic_task_models(monkeypatch) -> None:
    monkeypatch.setenv("TEST_KEY", "sk-abc")
    assert LLMConfig(_ANTHROPIC, "enrich").model == "haiku"
    assert LLMConfig(_ANTHROPIC, "rank").model == "sonnet"
    assert LLMConfig(_ANTHROPIC, "enrich").api_key == "sk-abc"


def test_llmconfig_rejects_unknown_provider() -> None:
    with pytest.raises(LLMConfigError, match="must be one of"):
        LLMConfig({"provider": "banana"}, "enrich")


def test_llmconfig_key_required_only_when_used(monkeypatch) -> None:
    monkeypatch.delenv("TEST_KEY", raising=False)
    cfg = LLMConfig(_ANTHROPIC, "enrich")  # construction is fine (dry-run needs no key)
    assert cfg.needs_key is True
    with pytest.raises(LLMConfigError, match="needs TEST_KEY"):
        cfg.require_key()


def test_llmconfig_local_provider_needs_no_key() -> None:
    cfg = LLMConfig(_OLLAMA, "enrich")
    assert cfg.provider == "ollama"
    assert cfg.api_key is None
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.request_json_object is True


def test_llmconfig_json_format() -> None:
    assert LLMConfig(_OLLAMA, "enrich").request_json_object is True
    # LM Studio defaults to prompt-only JSON
    assert (
        LLMConfig(
            {"provider": "lmstudio", "lmstudio": {"model": "m"}}, "enrich"
        ).request_json_object
        is False
    )
    forced = {"provider": "lmstudio", "lmstudio": {"model": "m", "json_format": "json_object"}}
    assert LLMConfig(forced, "enrich").request_json_object is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Here you go:\n{"a": 1}\nhope that helps', {"a": 1}),
    ],
)
def test_extract_json(raw: str, expected: dict) -> None:
    assert _extract_json(raw) == expected


def test_extract_json_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="expected a JSON object"):
        _extract_json("[1, 2, 3]")


class _FakeProvider(Provider):
    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls = 0

    def complete(self, **kwargs) -> LLMResult:
        text = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return LLMResult(text=text, model="fake")


def _use_provider(monkeypatch, provider: Provider) -> None:
    monkeypatch.setattr(client_mod, "load_settings", lambda: _Settings())
    monkeypatch.setattr(client_mod, "_build_provider", lambda cfg: provider)


class _Settings:
    def llm(self) -> dict:
        return _OLLAMA


def test_complete_json_dry_run_makes_no_call(monkeypatch, capsys) -> None:
    monkeypatch.setattr(client_mod, "load_settings", lambda: _Settings())
    out = complete_json(system="s", user="u", dry_run=True)
    assert out == {}
    assert "dry-run" in capsys.readouterr().out


def test_complete_json_parses_reply(monkeypatch) -> None:
    fake = _FakeProvider(['{"ok": true}'])
    _use_provider(monkeypatch, fake)
    assert complete_json(system="s", user="u") == {"ok": True}
    assert fake.calls == 1


def test_complete_json_retries_once_on_bad_json(monkeypatch) -> None:
    fake = _FakeProvider(["not json at all", '{"ok": 1}'])
    _use_provider(monkeypatch, fake)
    assert complete_json(system="s", user="u") == {"ok": 1}
    assert fake.calls == 2


def test_complete_json_gives_up_after_retry(monkeypatch) -> None:
    fake = _FakeProvider(["nope", "still nope"])
    _use_provider(monkeypatch, fake)
    with pytest.raises(LLMError, match="did not return valid JSON"):
        complete_json(system="s", user="u")
    assert fake.calls == 2

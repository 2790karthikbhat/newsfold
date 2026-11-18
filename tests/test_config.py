from __future__ import annotations

import pytest

from newsfold.config import _interpolate_env, load_settings


@pytest.mark.parametrize(
    ("value", "env", "expected"),
    [
        ("${FOO}", {"FOO": "bar"}, "bar"),
        ("${FOO}", {}, ""),
        ("${FOO:-default}", {}, "default"),
        ("${FOO:-default}", {"FOO": "set"}, "set"),
        ("${FOO:-}", {}, ""),
        ("prefix-${FOO:-x}-suffix", {}, "prefix-x-suffix"),
        ("${A}/${B:-b}", {"A": "a"}, "a/b"),
        ("plain value", {}, "plain value"),
        ("http://localhost:1234/v1", {}, "http://localhost:1234/v1"),  # no false positives
    ],
)
def test_interpolate_env_strings(monkeypatch, value, env, expected) -> None:
    for k in ("FOO", "A", "B"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert _interpolate_env(value) == expected


def test_interpolate_env_recurses_into_dict_and_list(monkeypatch) -> None:
    monkeypatch.setenv("X", "resolved")
    data = {"a": "${X}", "b": ["${X}", "${Y:-fallback}"], "c": 42, "d": None}
    assert _interpolate_env(data) == {
        "a": "resolved",
        "b": ["resolved", "fallback"],
        "c": 42,
        "d": None,
    }


def test_load_settings_llm_provider_env_override(monkeypatch) -> None:
    monkeypatch.setenv("NEWSFOLD_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("NEWSFOLD_LLM_MODEL", "test-model")
    load_settings.cache_clear()
    try:
        block = load_settings().llm()
        assert block["provider"] == "ollama"
        assert block["ollama"]["model"] == "test-model"
    finally:
        monkeypatch.delenv("NEWSFOLD_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("NEWSFOLD_LLM_MODEL", raising=False)
        load_settings.cache_clear()


def test_load_settings_defaults_when_no_env_set(monkeypatch) -> None:
    for var in (
        "NEWSFOLD_LLM_PROVIDER",
        "NEWSFOLD_LLM_MODEL",
        "NEWSFOLD_LLM_RANK_MODEL",
        "NEWSFOLD_LLM_BASE_URL",
        "NEWSFOLD_LLM_JSON_FORMAT",
    ):
        monkeypatch.delenv(var, raising=False)
    load_settings.cache_clear()
    try:
        block = load_settings().llm()
        assert block["provider"]  # a real provider name, not "" or a literal ${...}
        assert "${" not in str(block)
    finally:
        load_settings.cache_clear()


def test_packaged_pricing_parses(monkeypatch, tmp_path) -> None:
    import newsfold.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)  # force the packaged default
    config_mod.load_pricing.cache_clear()
    try:
        pricing = config_mod.load_pricing()
        assert pricing["anthropic"]["claude-haiku-4-5"].output == 5.0
        assert "azure" not in pricing  # empty table is skipped
    finally:
        config_mod.load_pricing.cache_clear()


def test_pricing_rejects_non_numeric(monkeypatch, tmp_path) -> None:
    import newsfold.config as config_mod

    (tmp_path / "pricing.yaml").write_text("openai:\n  m: {input: cheap, output: 1}\n")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    config_mod.load_pricing.cache_clear()
    try:
        with pytest.raises(config_mod.ConfigError, match="numeric"):
            config_mod.load_pricing()
    finally:
        config_mod.load_pricing.cache_clear()

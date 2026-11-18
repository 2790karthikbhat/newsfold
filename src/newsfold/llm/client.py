"""The single entry point for LLM calls.

`complete_json` builds the configured provider, sends one prompt, and returns a
parsed JSON object — with a one-shot retry if the first reply isn't valid JSON.
`dry_run=True` prints the plan and returns ``{}`` without touching the network, so
every stage stays runnable offline.

Provider + model come from `config/settings.yaml` `[llm]`; nothing else in the
codebase talks to a model backend.
"""

from __future__ import annotations

import json
import os
from typing import Any

from newsfold.config import load_settings
from newsfold.llm.anthropic_provider import AnthropicProvider
from newsfold.llm.base import LLMConfigError, LLMError, Provider
from newsfold.llm.openai_compat import OpenAICompatProvider
from newsfold.llm.usage import UsageLog

_DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {"base_url": "https://api.anthropic.com", "api_key_env": "ANTHROPIC_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    # LM Studio's structured-output support varies by engine (MLX rejects `json_object`);
    # default it off and lean on the prompt. Override with `json_format: json_object`.
    "lmstudio": {"base_url": "http://localhost:1234/v1", "api_key_env": "", "json_format": "text"},
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key_env": ""},
    # base_url = the resource endpoint, https://{resource}.openai.azure.com — no default.
    "azure": {"base_url": "", "api_key_env": "AZURE_OPENAI_API_KEY"},
}
# Latest GA data-plane api-version for /openai/deployments/{deployment}/... (Microsoft
# Learn, "Azure OpenAI REST API reference", 2024-10-21 GA). `api_version: v1` switches
# to the versionless {endpoint}/openai/v1/ form instead.
AZURE_DEFAULT_API_VERSION = "2024-10-21"
_HOSTED = {"anthropic", "openai", "azure"}
_JSON_HINT = "Reply with a single JSON object and nothing else — no prose, no code fences."


class LLMConfig:
    """Resolved `[llm]` config for one task (`enrich` or `rank`)."""

    def __init__(self, raw: dict[str, Any], task: str) -> None:
        provider = str(raw.get("provider", "")).strip().lower()
        if provider not in _DEFAULTS:
            raise LLMConfigError(
                f"settings.yaml [llm].provider must be one of {sorted(_DEFAULTS)}, got {provider!r}"
            )
        block = dict(raw.get(provider) or {})
        defaults = _DEFAULTS[provider]

        self.provider = provider
        self.timeout = float(raw.get("request_timeout_sec", 120))
        self.base_url = str(
            block.get("base_url") or block.get("endpoint") or defaults["base_url"]
        ).rstrip("/")
        self.api_version = str(block.get("api_version") or AZURE_DEFAULT_API_VERSION)
        self.max_tokens = int(raw.get("max_tokens", 2048))
        json_format = str(block.get("json_format") or defaults.get("json_format", "json_object"))
        self.request_json_object = json_format.lower() == "json_object"

        # Azure calls the model a "deployment"; accept either spelling.
        model = (
            block.get(f"{task}_model")
            or block.get(f"{task}_deployment")
            or block.get("model")
            or block.get("deployment")
        )
        if not model:
            raise LLMConfigError(
                f"settings.yaml [llm].{provider} needs a 'model' (or '{task}_model')"
            )
        self.model = str(model)

        self.api_key_env = str(block.get("api_key_env") or defaults["api_key_env"])
        self.api_key = os.environ.get(self.api_key_env) if self.api_key_env else None
        self.needs_key = provider in _HOSTED

    def require_key(self) -> str:
        if not self.api_key:
            raise LLMConfigError(f"{self.provider} needs {self.api_key_env} set (see .env.example)")
        return self.api_key


def azure_url(endpoint: str, deployment: str, api_version: str) -> tuple[str, dict[str, str]]:
    """(base_url, query) for Azure OpenAI. `{endpoint}/openai/deployments/{deployment}`
    + `?api-version=` for dated versions; `{endpoint}/openai/v1` (model = deployment
    name in the body, no api-version) when api_version is "v1" or the endpoint already
    ends in /openai/v1."""
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/openai/v1"):
        return endpoint, {}
    if api_version.lower() == "v1":
        return f"{endpoint}/openai/v1", {}
    return f"{endpoint}/openai/deployments/{deployment}", {"api-version": api_version}


def _build_provider(cfg: LLMConfig) -> Provider:
    if cfg.provider == "anthropic":
        return AnthropicProvider(
            base_url=cfg.base_url, api_key=cfg.require_key(), timeout=cfg.timeout
        )
    if cfg.provider == "azure":
        if not cfg.base_url:
            raise LLMConfigError(
                "azure needs [llm].azure.endpoint (https://<resource>.openai.azure.com) — "
                "set AZURE_OPENAI_ENDPOINT"
            )
        base_url, params = azure_url(cfg.base_url, cfg.model, cfg.api_version)
        return OpenAICompatProvider(
            name="azure",
            base_url=base_url,
            api_key=cfg.require_key(),
            timeout=cfg.timeout,
            api_key_header="api-key",
            query_params=params,
        )
    return OpenAICompatProvider(
        name=cfg.provider,
        base_url=cfg.base_url,
        api_key=cfg.require_key() if cfg.needs_key else cfg.api_key,
        timeout=cfg.timeout,
    )


def _extract_json(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```", 2)[1]
        body = body[4:].strip() if body.lower().startswith("json") else body.strip()
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        body = body[start : end + 1]
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def describe(task: str = "enrich") -> str:
    cfg = LLMConfig(load_settings().llm(), task)
    return f"{cfg.provider}:{cfg.model}"


def complete_json(
    *,
    system: str,
    user: str,
    task: str = "enrich",
    dry_run: bool = False,
    max_tokens: int | None = None,
    temperature: float = 0.0,
    usage: UsageLog | None = None,
) -> dict[str, Any]:
    """`usage`, when given, gets one record per provider call (the JSON retry too)."""
    cfg = LLMConfig(load_settings().llm(), task)
    tokens = max_tokens or cfg.max_tokens

    if dry_run:
        print(
            f"[dry-run] {cfg.provider}:{cfg.model} ({task}) — "
            f"system {len(system)} chars, user {len(user)} chars"
        )
        return {}

    provider = _build_provider(cfg)
    full_system = f"{system}\n\n{_JSON_HINT}"
    last_error: Exception | None = None
    for attempt in (1, 2):
        result = provider.complete(
            system=full_system,
            user=user,
            model=cfg.model,
            max_tokens=tokens,
            temperature=temperature,
            json_mode=cfg.request_json_object,
        )
        if usage is not None:
            usage.record(
                stage=task,
                provider=cfg.provider,
                model=cfg.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
            )
        try:
            return _extract_json(result.text)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt == 1:
                user = f"{user}\n\nYour previous reply was not valid JSON. {_JSON_HINT}"
    raise LLMError(f"{cfg.provider}:{cfg.model} did not return valid JSON: {last_error}")

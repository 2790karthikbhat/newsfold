"""Load and validate config/*.yaml into typed objects. The only place YAML is parsed."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

APP_NAME = "newsfold"
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULTS_DIR = PACKAGE_DIR / "defaults"
MIGRATIONS_DIR = PACKAGE_DIR / "migrations"
REPO_ROOT = PACKAGE_DIR.parents[1]
# Running from a source checkout (src/ layout, incl. the Docker image) vs. an
# installed wheel (pipx / pip): a checkout keeps config/, data/, out/ in the repo.
IS_SOURCE_CHECKOUT = PACKAGE_DIR.parent.name == "src"


def resolve_dirs(
    env: Mapping[str, str], *, source_checkout: bool, repo_root: Path
) -> tuple[Path, Path]:
    """(home, config_dir). `home` holds data/ (SQLite, caches) and out/ (digests).

    NEWSFOLD_HOME / NEWSFOLD_CONFIG_DIR override everything; a source checkout
    uses the repo itself; an installed copy follows XDG (~/.local/share/<app>,
    ~/.config/<app>)."""
    user_home = Path(env.get("HOME") or Path.home())
    explicit_home = env.get("NEWSFOLD_HOME")
    if explicit_home:
        home = Path(explicit_home).expanduser()
    elif source_checkout:
        home = repo_root
    else:
        home = Path(env.get("XDG_DATA_HOME") or user_home / ".local" / "share") / APP_NAME

    explicit_config = env.get("NEWSFOLD_CONFIG_DIR")
    if explicit_config:
        config_dir = Path(explicit_config).expanduser()
    elif explicit_home or source_checkout:
        config_dir = home / "config"
    else:
        config_dir = Path(env.get("XDG_CONFIG_HOME") or user_home / ".config") / APP_NAME
    return home, config_dir


if IS_SOURCE_CHECKOUT:
    load_dotenv(REPO_ROOT / ".env")
HOME_DIR, CONFIG_DIR = resolve_dirs(
    os.environ, source_checkout=IS_SOURCE_CHECKOUT, repo_root=REPO_ROOT
)
load_dotenv(CONFIG_DIR / ".env")
DATA_DIR = HOME_DIR / "data"
OUT_DIR = HOME_DIR / "out"
CONFIG_FILES = ("settings.yaml", "topics.yaml", "sources.yaml", "tiers.yaml", "pricing.yaml")

_VALID_MODES = {"persistent", "temporary"}

# ${VAR} / ${VAR:-default} in any YAML string value is substituted from the
# environment (.env included) — shell-style, so a value can be overridden without
# editing the tracked config file. ${VAR} with no default and an unset env var
# resolves to "" (same as unset shell expansion), not left as literal text.
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")


def _interpolate_env(value: Any) -> Any:
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            name, has_default, default = match.group(1), match.group(2), match.group(3)
            if name in os.environ:
                return os.environ[name]
            return default if has_default is not None else ""

        return _ENV_VAR_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


class ConfigError(RuntimeError):
    """Raised when a config file is missing required fields or has bad values."""


@dataclass(frozen=True)
class TopicConfig:
    keyword: str
    mode: str
    languages: tuple[str, ...]


@dataclass(frozen=True)
class SourceConfig:
    input_url: str
    name: str | None
    topic: str | None


@dataclass(frozen=True)
class Settings:
    db_url: str
    target_language: str
    raw: dict[str, Any] = field(repr=False)

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.raw.get(name, {}))

    def llm(self) -> dict[str, Any]:
        block = self.section("llm")
        if not block:
            raise ConfigError("settings.yaml: an [llm] section is required for LLM stages")
        return block

    def effective_db_url(self) -> str:
        """Env override wins over settings.yaml. Relative sqlite paths are anchored to HOME_DIR."""
        url = os.environ.get("NEWSFOLD_DB_URL") or self.db_url
        prefix = "sqlite:///"
        if url.startswith(prefix) and not url.startswith(prefix + "/"):
            rel = url[len(prefix) :]
            return f"{prefix}{(HOME_DIR / rel).as_posix()}"
        return url


def config_path(name: str) -> Path:
    """The user's copy in CONFIG_DIR if present, else the packaged default."""
    path = CONFIG_DIR / name
    return path if path.exists() else DEFAULTS_DIR / name


def _read(name: str) -> dict[str, Any]:
    path = config_path(name)
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open() as fh:
        data = yaml.safe_load(fh)
    data = data if isinstance(data, dict) else {}
    interpolated: dict[str, Any] = _interpolate_env(data)
    return interpolated


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    data = _read("settings.yaml")
    if "db_url" not in data:
        raise ConfigError("settings.yaml: 'db_url' is required")
    return Settings(
        db_url=data["db_url"],
        target_language=data.get("target_language", "en"),
        raw=data,
    )


@lru_cache(maxsize=1)
def load_topics() -> tuple[TopicConfig, ...]:
    data = _read("topics.yaml")
    entries = data.get("topics") or []
    if not entries:
        raise ConfigError("topics.yaml: at least one topic is required")

    out: list[TopicConfig] = []
    seen: set[str] = set()
    for i, t in enumerate(entries):
        where = f"topics.yaml entry {i}"
        try:
            keyword = str(t["keyword"]).strip()
            mode = str(t["mode"]).strip()
            languages = tuple(str(x).strip() for x in t["languages"])
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"{where}: missing/invalid field ({exc})") from exc
        if not keyword:
            raise ConfigError(f"{where}: keyword is empty")
        if mode not in _VALID_MODES:
            raise ConfigError(f"{where}: mode must be one of {sorted(_VALID_MODES)}")
        if not languages:
            raise ConfigError(f"{where}: languages must be non-empty")
        if keyword.lower() in seen:
            raise ConfigError(f"{where}: duplicate keyword {keyword!r}")
        seen.add(keyword.lower())
        out.append(TopicConfig(keyword=keyword, mode=mode, languages=languages))
    return tuple(out)


@lru_cache(maxsize=1)
def load_sources() -> tuple[SourceConfig, ...]:
    data = _read("sources.yaml")
    out: list[SourceConfig] = []
    for i, s in enumerate(data.get("sources") or []):
        where = f"sources.yaml entry {i}"
        try:
            input_url = str(s["input_url"]).strip()
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"{where}: missing 'input_url' ({exc})") from exc
        if not input_url:
            raise ConfigError(f"{where}: input_url is empty")
        name = str(s["name"]).strip() if s.get("name") else None
        topic = str(s["topic"]).strip() if s.get("topic") else None
        out.append(SourceConfig(input_url=input_url, name=name, topic=topic))
    return tuple(out)


@lru_cache(maxsize=1)
def load_tiers() -> dict[str, Any]:
    return _read("tiers.yaml")


def init_config_dir(*, force: bool = False) -> list[tuple[Path, str]]:
    """Copy the packaged default config files into CONFIG_DIR so they can be edited.
    Existing files are kept unless `force`. Returns (path, "created"|"overwritten"|"kept")."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[Path, str]] = []
    for name in CONFIG_FILES:
        src, dest = DEFAULTS_DIR / name, CONFIG_DIR / name
        if dest.exists() and not force:
            results.append((dest, "kept"))
            continue
        action = "overwritten" if dest.exists() else "created"
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        results.append((dest, action))
    return results


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    output: float


@lru_cache(maxsize=1)
def load_pricing() -> dict[str, dict[str, ModelPrice]]:
    data = _read("pricing.yaml")
    out: dict[str, dict[str, ModelPrice]] = {}
    for provider, models in data.items():
        if not models:
            continue
        if not isinstance(models, dict):
            raise ConfigError(f"pricing.yaml: '{provider}' must map model ids to prices")
        table: dict[str, ModelPrice] = {}
        for model, price in models.items():
            try:
                table[str(model)] = ModelPrice(
                    input=float(price["input"]), output=float(price["output"])
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(
                    f"pricing.yaml: {provider}.{model} needs numeric input/output ({exc})"
                ) from exc
        out[str(provider).lower()] = table
    return out

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

import newsfold.config as config_mod
from newsfold.config import CONFIG_FILES, DEFAULTS_DIR, resolve_dirs
from newsfold.db import sqlite_file, upgrade_db

REPO = Path("/repo")


def test_source_checkout_uses_repo_layout() -> None:
    home, cfg = resolve_dirs({"HOME": "/u"}, source_checkout=True, repo_root=REPO)
    assert (home, cfg) == (REPO, REPO / "config")


def test_installed_follows_xdg_defaults() -> None:
    home, cfg = resolve_dirs({"HOME": "/u"}, source_checkout=False, repo_root=REPO)
    assert home == Path("/u/.local/share/newsfold")
    assert cfg == Path("/u/.config/newsfold")


def test_installed_honours_xdg_vars() -> None:
    env = {"HOME": "/u", "XDG_DATA_HOME": "/xd", "XDG_CONFIG_HOME": "/xc"}
    home, cfg = resolve_dirs(env, source_checkout=False, repo_root=REPO)
    assert (home, cfg) == (Path("/xd/newsfold"), Path("/xc/newsfold"))


@pytest.mark.parametrize("checkout", [True, False])
def test_newsfold_home_overrides_both(checkout: bool) -> None:
    home, cfg = resolve_dirs(
        {"HOME": "/u", "NEWSFOLD_HOME": "/srv/news"}, source_checkout=checkout, repo_root=REPO
    )
    assert (home, cfg) == (Path("/srv/news"), Path("/srv/news/config"))


def test_config_dir_override_is_independent() -> None:
    env = {"HOME": "/u", "NEWSFOLD_CONFIG_DIR": "/etc/news"}
    home, cfg = resolve_dirs(env, source_checkout=False, repo_root=REPO)
    assert home == Path("/u/.local/share/newsfold")
    assert cfg == Path("/etc/news")


def test_every_config_file_has_a_packaged_default() -> None:
    for name in CONFIG_FILES:
        assert (DEFAULTS_DIR / name).is_file(), name


def test_config_path_prefers_user_copy_then_default(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    assert config_mod.config_path("topics.yaml") == DEFAULTS_DIR / "topics.yaml"
    (tmp_path / "topics.yaml").write_text("topics: []\n")
    assert config_mod.config_path("topics.yaml") == tmp_path / "topics.yaml"


def test_init_config_dir_copies_and_keeps(monkeypatch, tmp_path) -> None:
    target = tmp_path / "cfg"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", target)
    first = config_mod.init_config_dir()
    assert {a for _, a in first} == {"created"}
    (target / "topics.yaml").write_text("mine\n")
    second = dict(config_mod.init_config_dir())
    assert second[target / "topics.yaml"] == "kept"
    assert (target / "topics.yaml").read_text() == "mine\n"
    forced = dict(config_mod.init_config_dir(force=True))
    assert forced[target / "topics.yaml"] == "overwritten"


def test_sqlite_file() -> None:
    assert sqlite_file("sqlite:////abs/x.db") == Path("/abs/x.db")
    assert sqlite_file("sqlite:///:memory:") is None
    assert sqlite_file("sqlite://") is None
    assert sqlite_file("postgresql://u@h/db") is None


def test_upgrade_db_creates_schema_from_packaged_migrations(tmp_path) -> None:
    db_path = tmp_path / "nested" / "news.db"
    url = f"sqlite:///{db_path}"
    upgrade_db(url)
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"articles", "topics", "sources", "runs", "llm_usage", "alembic_version"} <= tables


def test_schema_is_current_tracks_head(tmp_path) -> None:
    from alembic import command
    from alembic.config import Config

    from newsfold.config import MIGRATIONS_DIR
    from newsfold.db import schema_is_current

    url = f"sqlite:///{tmp_path / 'news.db'}"
    upgrade_db(url)
    assert schema_is_current(url)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "-1")
    assert not schema_is_current(url)

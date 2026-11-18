"""Engine + Session factory. SQLite by default; the URL is the only Postgres-specific change."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from newsfold.config import MIGRATIONS_DIR, load_settings

_engine = None
_Session: sessionmaker[Session] | None = None

_SQLITE_PREFIX = "sqlite:///"


def sqlite_file(url: str) -> Path | None:
    """The on-disk file behind a `sqlite:///…` URL, or None (memory / other backends)."""
    if not url.startswith(_SQLITE_PREFIX):
        return None
    rest = url[len(_SQLITE_PREFIX) :].split("?", 1)[0]
    return Path(rest) if rest and rest != ":memory:" else None


def upgrade_db(url: str | None = None) -> str:
    """`alembic upgrade head` against the configured DB using the packaged migrations —
    works from an installed wheel, no alembic.ini needed. Returns the URL used."""
    from alembic import command
    from alembic.config import Config

    url = url or load_settings().effective_db_url()
    db_file = sqlite_file(url)
    if db_file is not None:
        db_file.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


def schema_is_current(url: str) -> bool:
    """True when the DB's alembic revision equals the packaged head."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    heads = set(ScriptDirectory.from_config(cfg).get_heads())
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            current = set(MigrationContext.configure(conn).get_current_heads())
    finally:
        engine.dispose()
    return current == heads


def _session_factory() -> sessionmaker[Session]:
    global _engine, _Session
    if _Session is None:
        url = load_settings().effective_db_url()
        db_file = sqlite_file(url)
        if db_file is not None and not db_file.exists():
            # Fresh install: create the DB at head. An existing DB is never migrated
            # implicitly — that stays an explicit `digest migrate`.
            upgrade_db(url)
        elif db_file is not None and not schema_is_current(url):
            print(
                "warning: database schema is behind this version — run `digest migrate`",
                file=sys.stderr,
            )
        _engine = create_engine(url, future=True)
        _Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

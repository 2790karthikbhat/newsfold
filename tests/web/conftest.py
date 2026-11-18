"""Web test fixtures. Routes call `session_scope()`, which is monkeypatched to bind
to an in-memory SQLite engine shared (via StaticPool) across the multiple
connections a real request cycle opens (the seeding session, plus one per request —
FastAPI runs sync routes in a worker thread)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import newsfold.db as db_mod
from newsfold.models import Base


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "_Session", factory)
    return factory


@pytest.fixture
def db(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client(session_factory):
    from newsfold.web.app import create_app

    return TestClient(create_app())

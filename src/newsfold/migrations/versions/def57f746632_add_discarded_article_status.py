"""add discarded article status

Adds the ``discarded`` value to ``ArticleStatus`` (LLM rank/classify drop, kept for
audit). On SQLite ``status`` is a plain VARCHAR with no CHECK constraint, so the new
value needs no DDL — this migration only widens the column for headroom and to keep
the schema honest for the Phase-2 Postgres move.

Revision ID: def57f746632
Revises: 012cfc621da9
Create Date: 2026-09-09 16:26:36.613905
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "def57f746632"
down_revision: str | Sequence[str] | None = "012cfc621da9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("articles") as batch:
        batch.alter_column("status", type_=sa.String(length=16), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("articles") as batch:
        batch.alter_column("status", type_=sa.String(length=9), existing_nullable=False)

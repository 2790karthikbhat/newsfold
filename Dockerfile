# Personal single-user deployment image — runs `digest serve` (the long-lived
# web/Glance process) and doubles as the target for one-shot pipeline commands
# via `docker compose exec newsfold digest run` (see docs/deploy-server.md).
# Not a hosted/multi-user service — see CLAUDE.md's "Is/Is not".

FROM python:3.15-slim AS builder

RUN pip install --no-cache-dir poetry==2.1.4
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1

WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-directory

COPY src ./src
COPY alembic.ini README.md LICENSE ./
RUN poetry install --only main


FROM python:3.15-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/2790karthikbhat/newsfold" \
      org.opencontainers.image.description="Multi-source, multi-language news -> one translated, ranked digest" \
      org.opencontainers.image.licenses="MIT"

RUN useradd --create-home --uid 1000 localnews
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/src ./src
COPY --from=builder /app/alembic.ini ./
# No config/ is baked in: the packaged defaults (src/newsfold/defaults/) apply
# unless ./config is bind-mounted (docker-compose.yml) — per file, so mounting
# just a topics.yaml is enough.
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# data/ (SQLite DB, sports cache) and out/ (rendered digests) are bind-mounted
# by docker-compose.yml — created here so the mount points exist with the
# right ownership even before the first mount.
RUN mkdir -p data out config && chown -R localnews:localnews /app
USER localnews

EXPOSE 8765
# `digest migrate` (alembic upgrade head) runs once here, at container start (idempotent — a
# no-op once the DB is current) — NOT on every `docker compose exec
# newsfold <command>` one-shot invocation, which bypasses ENTRYPOINT
# entirely and talks to the venv's `digest` script directly.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765"]

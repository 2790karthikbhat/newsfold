# Newsfold — Engineering Context

> Replace "scroll Facebook → find article → translate → read" with an automated
> per-topic digest: fetch → decode → cluster → rank → translate → summarize → render.

This file is the *how we build it* layer: scope, layer rules, invariants.

**Asked to install or set up newsfold for someone?** Follow the setup playbook in
[`AGENTS.md`](AGENTS.md); this file is for changing the code.

---

## What this is (and is not)

**Is:** a personal, scheduled Python pipeline that produces one local digest file
(HTML/Markdown) per run, covering a set of persistent + temporary topics.

**Is not:** a hosted multi-user service, an authenticated app, or a notification
system. The core is one command (`digest run`) against a local SQLite DB. Around it
sit three deliberately narrow extras:

- a **local-only** web UI (`digest serve`) for reading digest history and managing
  topics/sources/articles;
- a small **sports-of-interest** module (`digest sports fetch`) plus two
  Glance-dashboard `extension`-widget endpoints (`/glance/digest`, `/glance/sports`);
- `Dockerfile` / `docker-compose.yml` / `deploy/` (systemd timers) to run it on a
  personal always-on server: still single-user, no auth, one SQLite DB
  (`docs/deploy-server.md`).

The design filter: _"Does this help turn noisy multi-source news into one calm,
translated, ranked read?"_ Yes → belongs. No → out of scope for now.

---

## Scope boundary

- **In scope:** standalone CLI pipeline, SQLite, local file output, the extras above.
- **Out of scope (do not build without sign-off):** a hosted/multi-user version
  (Postgres, Celery/Redis workers, auth), auto-expiry of temporary topics, push
  delivery. The SQLAlchemy models and pipeline stages are written so a later move to
  Postgres + a task queue is mechanical, but no such infra, config, or abstractions
  land speculatively.

Everything in "Deliberate non-features" below is a **decision, not a gap**.
Revisiting it requires a deliberate conversation.

---

## Project layout

```
newsfold/
├── config/                 # YOUR overrides of src/newsfold/defaults/*.yaml. Git-ignored
│                          # (`digest init` creates it); any missing file → packaged default.
├── data/                   # SQLite DB lives here. Git-ignored.
├── out/                    # Rendered digests: digest-YYYY-MM-DD.html. Git-ignored.
├── src/newsfold/
│   ├── defaults/            # Packaged, generic starter config. Git-tracked, ships in the wheel.
│   │   ├── topics.yaml       # persistent + temporary topics
│   │   ├── sources.yaml      # example user-added feeds; DB is source of truth
│   │   ├── tiers.yaml        # per-topic-area tier-1 domain allowlist
│   │   └── settings.yaml     # target reading language, per-run caps, model ids
│   ├── migrations/          # Alembic env + versions/ (packaged so `digest migrate` works
│   │                        # from a pipx install; alembic.ini points here for dev)
│   ├── config.py            # path resolution (HOME_DIR/CONFIG_DIR) + load/validate YAML
│   ├── db.py                # engine + Session factory, upgrade_db() (SQLite now, Postgres later)
│   ├── models.py            # SQLAlchemy ORM: Topic, Source, Article, Run
│   ├── articles.py          # reader lifecycle: read/unread, archive, save, delete, retention
│   ├── cli.py               # Typer app — every pipeline stage is a subcommand
│   ├── ingest/
│   │   ├── google_news.py    # build RSS search URLs, parse with feedparser
│   │   ├── decode.py         # resolve Google News redirect blobs → real URLs
│   │   └── user_sources.py   # website URL → resolved feed URL, feed autodiscovery
│   ├── rank/
│   │   ├── cluster.py        # keyword-overlap / same-day clustering, no LLM
│   │   ├── score.py          # rank_score: corroboration + tier bonus + recency
│   │   └── llm_rank.py       # one batched newsworthiness call per topic
│   ├── process/
│   │   ├── extract.py        # trafilatura full-text fetch + headline-only fallback
│   │   └── enrich.py         # one call/article: classify + translate + summarize
│   ├── digest/
│   │   ├── render.py         # group by topic, order by llm_rank, write file
│   │   └── templates/digest.html.j2
│   ├── web/
│   │   ├── app.py             # create_app()/run_server() — the only place FastAPI is built
│   │   ├── digest_history.py   # list/read rendered files in out/ — read-only
│   │   ├── routes/             # home.py, digest.py, topics.py, sources.py, glance.py
│   │   └── templates/          # sidebar shell (base.html.j2) + one template per page; own
│   │                          # chrome, does not import digest/'s digest.html.j2. Visual
│   │                          # design follows docs/paper-and-ink-design-spec.md's tokens
│   │                          # (warm paper palette, serif reading type, one ribbon accent
│   │                          # — no per-topic color-coding, no drop shadows). Exception:
│   │                          # glance_*.html.j2 are bare fragments for Glance's `extension`
│   │                          # widget — they don't extend base.html.j2 and use Glance's own
│   │                          # CSS utility classes instead (see the web/ layer rule below).
│   ├── sports/
│   │   ├── models.py           # Event — one presentation-ready shape for football/cricket/F1
│   │   ├── cache.py            # file-based TTL cache (data/sports_cache/) — no DB table
│   │   ├── matching.py          # word-boundary team-name matching, shared by football.py/cricket.py
│   │   ├── football.py         # football-data.org, by competition code + team-name filter
│   │   ├── cricket.py          # bigballsdata.com, one request per day across the results/upcoming window
│   │   ├── cricinfo.py          # ESPN Cricinfo live-scores RSS — live-only supplement to cricket.py
│   │   ├── f1.py                # Jolpica-F1 (api.jolpi.ca) — free, no key
│   │   └── aggregate.py         # refresh_all() (writes cache) / load_cached() (reads it)
│   ├── llm/
│   │   ├── base.py           # Provider ABC, LLMResult, LLMError/LLMConfigError
│   │   ├── client.py         # complete_json — the only entry point; config → provider
│   │   ├── openai_compat.py  # OpenAI / Azure / LM Studio / Ollama (one shared shape)
│   │   └── anthropic_provider.py
│   ├── progress.py           # ProgressCallback + stage_progress (rich bar / plain)
│   └── pipeline.py           # `digest run` — chains every stage, one txn each
├── tests/                  # mirrors src/newsfold/ structure
└── docs/
    ├── deploy-server.md
    └── paper-and-ink-design-spec.md
```

### Layer rules (enforce strictly)

- **`config/` is declarative and dumb.** Parsing/validation lives in `config.py`,
  never scattered. The running system reads config once at startup.
- **Paths are resolved once, in `config.py`.** `HOME_DIR` holds `data/` + `out/`;
  `CONFIG_DIR` holds the user's YAML. Source checkout (src/ layout, incl. Docker) →
  repo root + `config/`; installed wheel → `~/.local/share/newsfold` +
  `~/.config/newsfold` (XDG vars honoured); `NEWSFOLD_HOME` /
  `NEWSFOLD_CONFIG_DIR` override either. Each YAML file is read from `CONFIG_DIR`
  if present, else from `src/newsfold/defaults/`. Nothing else builds a path
  from `REPO_ROOT`. Keep `defaults/` generic — personal topics belong in `config/`.
- **Any YAML string value may reference an env var:** `${VAR}` / `${VAR:-default}`,
  substituted in `config._read()` before the file's contents are used (shell-style;
  unset + no default → `""`). `config/settings.yaml`'s `[llm]` block uses this for
  `provider` / `model` / `rank_model` / `base_url` / `json_format` so switching
  provider or model needs an env var, not an edit to the tracked file — see
  `.env.example`.
- **`ingest/` produces rows, never opinions.** It fetches and normalizes into
  `Article` rows. No clustering, scoring, translation, or LLM calls here.
- **`rank/` never fetches and never calls an LLM except in `llm_rank.py`.**
  Clustering and scoring are pure functions over already-stored articles.
- **`process/` is the only place per-article LLM calls happen** (`enrich.py`), and
  the only place full article bodies are fetched (`extract.py`).
- **`digest/` is pure rendering.** Read from the DB, emit a file. No mutation.
- **`articles.py` is the only place reader-state (`read_state`, `saved`) or hard
  deletes happen** — same top-level-module pattern as `topics.py`/`sources.py`, not
  nested under a pipeline stage since it's reader-driven, not pipeline-driven.
  Manual functions (`mark_read`, `archive`, `save`, `delete`, ...) always do exactly
  what's asked, no guardrails — the caller (CLI/web) is a human acting
  deliberately. `run_cleanup` is the one *automated, unattended* path and stays
  conservative by construction (see the retention invariant below) — don't loosen
  its scoping to "simplify" it.
- **`web/` is the only place a local HTTP server is constructed and the only place
  topic/source/article-*mutating* endpoints live.** Route handlers parse the
  request, call one function in `topics.py` / `sources.py` / `articles.py`, and
  redirect — same "no business logic in the entry-point layer" rule as `cli.py`.
  `web/` may call `digest/render.build_digest` and `views.list_articles`
  read-only; `digest/` itself stays pure rendering and knows nothing about HTTP.
  Article action routes (`/digest/article/{id}/{read,unread,archive,unarchive,
  save,unsave,delete}`) take a `next` form field for the post-action redirect,
  validated to an internal path only (`_safe_next` — never trust it as an
  external URL). `routes/glance.py`'s two endpoints are read-only Glance
  `extension`-widget fragments — `/glance/digest` reuses `build_digest`;
  `/glance/sports` reuses `sports.aggregate.load_cached` and never calls a sports
  API itself. Their templates deliberately don't extend `base.html.j2` (Glance
  drops the fragment straight into its own page) and link back to newsfold
  pages with an **absolute** URL from `config/settings.yaml`'s `web.public_base_url`
  (`$NEWSFOLD_PUBLIC_BASE_URL`) — **never** from `request.base_url`. That header
  reflects how *Glance's backend* reached newsfold (e.g. Docker's
  `host.docker.internal`), not how *your browser* will — a Dockerized Glance
  otherwise produces links a browser can't resolve.
  `request.base_url` is only a same-machine, no-proxy fallback when
  `public_base_url` is unset.
- **`sports/` fetches and normalizes only — no DB, file-cache via `sports/cache.py`.**
  Each provider module (`football.py`/`cricket.py`/`f1.py`) exposes one
  `fetch_*(cfg, *, client=None) -> *Result` that never raises across its module
  boundary (an `ok`/`error` result, same "fetch failures are normal" spirit as
  `ingest`/`process`) and normalizes into the shared `Event` shape. Follows
  (teams/competitions to filter to) live in `config/settings.yaml`'s `sports:`
  section, read via `Settings.section("sports")` — no separate config file or
  loader needed for something this small. Football and cricket events are
  windowed to `sports.results_days`/`upcoming_days` after fetching (both
  providers return far more than "upcoming + recent" otherwise); F1 is exempt —
  full remaining calendar, by design. **Refreshed only by an explicit
  `digest sports fetch`, never inside `digest run`** — same "deliberate, not
  automatic" precedent as `retention`/`digest cleanup`. The web route
  (`/glance/sports`) only ever reads what that command cached; it never blocks a
  request on a live external call. `cricket.py` uses Big Balls Sports Data
  (bigballsdata.com), not CricAPI (dropped: CricAPI's free-tier match list is neither team-filterable
  nor sorted, so a bounded scan could — and did — miss a followed team's match
  entirely). Big Balls has no team filter either, but does support
  `?date=YYYY-MM-DD` for one *complete* UTC day, so `cricket.py` fetches one
  request per day across `results_days`..`upcoming_days` (not a page scan) —
  deterministic coverage of the configured window, not a sample. **Team
  matching across all providers (`sports/matching.py`) is word-boundary, not
  substring** — a naive `"india" in name` check matches inside "Mumbai
  **India**ns", a real false positive. **Known
  limitation (accepted):**
  bigballsdata's cricket archive has real coverage gaps for major
  international series (verified against their live API, not assumed — a
  real India vs Afghanistan T20I was simply absent from their data). No
  free-tier provider checked so far has had complete cricket coverage;
  don't build a multi-provider merge speculatively — only on request.
  **`cricinfo.py` is a narrow, deliberate exception** — a
  live-only supplement via ESPN Cricinfo's public livescores RSS feed
  (`cricinfo.com/rss/livescores.xml`), merged into the same `cricket` sport
  bucket in `aggregate.py`. It exists only to fill the "live match" card slot
  bigballsdata rarely has data for; it has no schedule or results data at all,
  and its items are one free-text title with team names and the live score
  run together (`"Team A 227/10  v Team B 174/3 *"`), not structured fields —
  team names are extracted with a best-effort `" v "` split for matching, and
  the raw title is kept as-is for display rather than further parsed.
- **`llm/` is the single chokepoint for model calls.** `llm/client.complete_json`
  is the only entry point; `llm/base.py` defines the provider contract and
  `openai_compat.py` / `anthropic_provider.py` are thin raw-httpx wrappers. Nothing
  outside `llm/` builds a request to a model backend. Provider + model are read from
  `config/settings.yaml` `[llm]` — never hard-coded.

---

## Core model invariants

Do not break these without updating this file:

- **Google News links are not real URLs until decoded.** Every `Article` with
  `source_type == "google_news"` MUST pass through `ingest/decode.py` before dedupe,
  tier lookup, or full-text fetch. Downstream code may assume `source_url` is a real
  publisher URL only when `source_type == "user_source"` or decoding has succeeded.
- **Fetch failures are normal, not exceptions.** One bad feed or page never aborts a
  run. What to set depends on the stage:
  - **decode** failure → `Article.status = broken` (the row is unusable — no real URL).
  - **extract** failure (paywall, bot-block, JS wall) → `Article.headline_only = True`,
    row keeps flowing; the summarize step works from the title. `broken` is *not* used
    here.
  - **feed discovery** failure → `Source.status = broken`, surfaced to the user.
- **LLM budget is bounded per run.** `enrich.py` = one call per surviving article;
  `llm_rank.py` = one call per topic per run. No per-article ranking calls, no
  per-field translation calls. A hard article cap (`settings.yaml`) applies, and
  `--dry-run` must print what *would* be sent without calling the API.
- **Runs are idempotent.** A second run the same day must not re-decode, re-fetch,
  re-translate, or re-pay for articles already processed. Dedupe on `source_url`
  (post-decode) and skip articles with `fetched_at` already set for the enrich step.
  `run_fetch` *also* dedupes by normalized title (topic-scoped, excludes `broken`
  rows) — not just `source_url` — because Google News's blob-encoded URL is not
  stable across separate fetches (the same real story gets re-issued under a
  different blob each time), so URL-only dedup silently misses it and wastes a
  real decode-stage round trip on a story you already have.
- **Per-item stages commit as they go, not once at the end.** `run_fetch` (per
  topic), `run_decode`/`run_extract`/`run_enrich` (per article), `run_llm_rank` (per
  topic) each call `session.commit()` after every item — not just at the end of
  their `session_scope()`. These stages are slow (serial network/LLM calls) and
  loop over many rows; a crash or Ctrl+C partway through must not roll back
  successes already made. The CLI wraps each in `_interruptible()`, which turns a
  `KeyboardInterrupt` into a clean "progress saved, re-run to continue" message
  instead of a traceback + lost work.
- **`source_type` and `source_name` are always populated** — they are the audit
  trail and the tier-weighting input.
- **Article status is a forward-only ladder:** `fetched → decoded` (google_news) or
  stays `fetched` (user_source) → `clustered` (cluster representative that survived
  scoring) → `processed` (translated + summarized). `discarded` is a terminal branch off
  `clustered` (LLM rank or classify dropped it — kept for audit). `broken` is a
  terminal branch off `fetched` (decode failed). `digest render` shows
  `clustered` + `processed`.
- **`read_state` (`unread → read → archived`) and `saved` are reader-driven, not
  pipeline-driven — orthogonal to `ArticleStatus` and to each other.** Only
  meaningful for articles a human could have seen (`status in (clustered,
  processed)`); everything else stays `unread` by construction. `saved` is
  independent of `read_state` (Gmail's star vs. read/unread, not a value on the same
  ladder) — never assume archiving or reading clears it, and never let automated
  cleanup touch a `saved` article regardless of age.
- **`digest render` defaults to `read_state == unread` only** — `read`/`archived`
  articles stop reappearing once you've acted on them. `--include-read` (CLI) /
  `include_read=True` (`build_digest`/`write_digest`) restores the full history.
  This is *the* fix for the digest otherwise growing unbounded with old stories.
- **Retention (`digest cleanup`) is deliberately conservative and never automatic.**
  It only runs when explicitly invoked (not inside `digest run`) and only ever does
  two things, both skipping `saved` articles unconditionally: auto-archive
  (`retention.auto_archive_after_days`) touches stale **unread** `clustered`/
  `processed` rows — reversible; auto-delete (`retention.auto_delete_after_days`)
  hard-removes old **`discarded`/`broken`** rows only — pipeline noise that was
  never shown to a reader. It never hard-deletes anything `unread`/`read`/
  `archived` no matter how old. Loosening that scope is a product decision, not a
  refactor — ask first.
- **Pipeline order** (`digest run`): fetch → decode → cluster → llm_rank → extract →
  enrich → render. `llm_rank` runs before `extract` so extraction only pays for the
  ranked survivors. `llm_rank` + `enrich` run only when `pipeline.LLM_STAGES_ENABLED`
  and a provider is configured.
- **Migrations are forward-only.** `alembic revision --autogenerate`, review before
  applying, never edit a migration that has run. They live in
  `src/newsfold/migrations/versions/`; `digest migrate` / `db.upgrade_db()` apply
  them without alembic.ini. A missing SQLite file is created at head on first use;
  an existing DB is never upgraded implicitly.

---

## Coding rules

**Toolchain:** Poetry (`pyproject.toml`). Do not use pip directly or add
`requirements*.txt`.

```bash
poetry install                   # install everything
poetry add <pkg>                 # runtime dep
poetry add --group dev <pkg>     # dev-only dep
poetry run pre-commit install    # first-time setup
poetry run pre-commit run --all-files
```

Code quality is enforced by **pre-commit**: Black, Ruff (lint + import sort,
includes bugbear + bandit), mypy (strict). Line length **100**. Never disable Black
or Ruff inline unless the reason is documented in a comment.

| Tool   | Role                   | Config (`pyproject.toml`)      |
| ------ | ---------------------- | ------------------------------ |
| Black  | Formatting             | `[tool.black]`                 |
| Ruff   | Lint + import sort     | `[tool.ruff]`                  |
| mypy   | Static typing (strict) | `[tool.mypy]`                  |
| pytest | Tests                  | `[tool.pytest.ini_options]`    |

**General**

- Business logic lives in the stage modules, not in `cli.py`. CLI commands parse
  args, call one function, print a result. Nothing more.
- Long-running stages (`fetch`, `decode`, `extract`, `llm_rank`, `enrich`) take an
  optional `progress: ProgressCallback` and call it `(done, total, note)` per item.
  The CLI wraps each in `progress.stage_progress()` (a live `rich` bar on a TTY, a
  throttled plain line for cron/pipes). Stage modules never import `rich`.
- DB access goes through `db.py`'s Session. Synchronous SQLAlchemy
  (the bottlenecks are rate-limited network calls, not DB throughput); an async
  swap is not worth pre-paying for. Where an I/O-bound
  stage benefits from concurrency (decode), reach for a bounded
  `ThreadPoolExecutor` around the blocking call instead of introducing `asyncio` —
  the Session itself is never touched from a worker thread, only from the thread
  that owns it.
- No comments in new code unless the logic is non-obvious or asked for.
- Type every public function signature. mypy runs strict.

**LLM calls**

- Every call goes through `llm.client.complete_json(...)`. It picks the provider
  from `config/settings.yaml` `[llm]`, sends one prompt, extracts a JSON object from
  the reply, and retries once if the first reply isn't valid JSON.
- Five providers, one contract: **anthropic** (raw Messages API), **openai**,
  **azure**, **lmstudio**, **ollama** (all four share the OpenAI chat-completions
  shape; azure only swaps the URL — `client.azure_url` — and the `api-key` header).
  Local providers need no API key. No `openai` / `anthropic` SDK — just `httpx`.
- `task=` selects the model: `[llm].<provider>.model` for `enrich`, optional
  `<provider>.rank_model` for `rank` (falls back to `model`).
- `dry_run=True` prints the plan and returns `{}` — no key needed, no network. Every
  LLM stage supports it; `digest run --dry-run` threads it through.
- Budget invariant holds: `enrich` = one call per surviving article (capped by
  `processing.max_articles_per_run`); `llm_rank` = one call per topic per run.
- A missing key for a hosted provider raises `LLMConfigError`; `digest run` catches
  it and finishes the non-LLM stages so a digest still lands.
- **Token usage is recorded, never estimated.** `complete_json(usage=UsageLog)` records
  every provider call (JSON retries included) from the response's `usage` block;
  calls without one count as calls with no tokens. Stages call
  `llm_usage.flush_usage()` right before each per-item commit (so Ctrl+C keeps the
  record of paid calls), keyed by run/stage/provider/model in `llm_usage`. Cost =
  tokens × `pricing.yaml` (USD per 1M); unknown model → "cost unknown", lmstudio /
  ollama → $0. Only seed prices verified from the provider's official page.

**Networking**

- `ingest/decode.py` against Google's endpoint: each decode still waits
  `decode_request_interval_sec` (~1s) before its own request — that per-request
  pacing is not negotiable, 429s cascade if it's shortened. What *is* configurable
  is how many paced decodes run at once: `ingestion.decode_concurrency` (default 3;
  `1` = fully serial) runs that many in a `ThreadPoolExecutor`, all DB writes and
  commits still happening on the session's own thread. Raise gradually, watch for a
  spike in `broken` results (429s), drop back to 1 if you see one. `--concurrency`
  on `digest decode` / `--decode-concurrency` on `digest run` override it per-run.
- `process/extract.py` against survivors' own publisher domains: same
  `ThreadPoolExecutor`-around-a-pure-function pattern as decode
  (`processing.extract_concurrency`, default 5; `--concurrency` on `digest extract`
  / `--extract-concurrency` on `digest run`), but there's no single shared endpoint
  here — survivor URLs scatter across many domains — so the ceiling is about being
  a reasonable crawler and bounding open connections, not dodging one origin's rate
  limit. Real-run check: 9 articles serial = 12.6s vs. concurrency=5 = 2.0s.
- All outbound HTTP has a timeout and a descriptive User-Agent.

---

## Testing

- Tests mirror `src/newsfold/`: `tests/ingest/`, `tests/rank/`, `tests/process/`,
  `tests/digest/`, `tests/web/`.
- `tests/web/` uses FastAPI's `TestClient` against an in-memory SQLite engine
  (`StaticPool` + `check_same_thread=False`, since a real request cycle opens more
  than one `Session` on it) with `newsfold.db._Session` monkeypatched to it —
  never against the real local DB.
- Pure functions (`cluster.py`, `score.py`, feed URL building, feed discovery
  path logic) get real unit tests with fixture data — no network.
- Network + LLM boundaries are mocked: canned RSS XML, canned decoder responses,
  canned Anthropic JSON. Real stage logic runs against them.
- `pytest-asyncio` not needed (sync codebase).

---

## Deliberate non-features — do not implement without explicit sign-off

| Feature                          | Why excluded                                    |
| -------------------------------- | ---------------------------------------------- |
| Facebook / social ingestion      | Graph API locked down; scraping violates ToS   |
| Auto-expiry of temporary topics  | Needs a persistent scheduler                   |
| Telegram / email / push delivery | Validate the core pipeline first               |
| Hosted / multi-user version (auth, Postgres/Celery/Redis) | Single-user tool; the local-only web UI (`digest serve`) and personal-server Docker setup are the extent of it |
| Async DB / Celery / Redis        | No concurrency need at this volume             |
| Large curated source-tier lists  | Grow the allowlist only from real-run noise    |
| Per-article LLM ranking calls    | Cost — ranking is one batched call per topic   |

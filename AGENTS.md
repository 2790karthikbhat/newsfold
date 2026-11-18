# AGENTS.md

Instructions for AI coding assistants (Claude Code, Codex, Cursor, Copilot, Gemini CLI,
…) working in this repo. Two jobs come up:

1. **Setting newsfold up for a person on their machine.** Follow the playbook below.
2. **Changing the code.** Read [`CLAUDE.md`](CLAUDE.md) first: it holds the layer
   rules and pipeline invariants every change must respect, whichever assistant you
   are. The checks are in "Working on the code" at the end of this file.

newsfold is a single-user CLI (`digest`). It follows topics through Google News RSS
plus the user's own feeds, decodes and de-duplicates links, clusters the same story
across outlets, optionally has an LLM rank/translate/summarize, and writes one
HTML/Markdown digest. State is one SQLite file. There is no server to sign up for.

---

## Setup playbook

### Ground rules

- **Ask before anything that changes the machine beyond this folder:** installing
  Python, pipx, Poetry or Docker; writing a crontab entry, launchd agent or systemd
  unit; publishing a port.
- **API keys:** the user puts them in the `.env` file themselves, or tells you to.
  Never echo a key back, never write one into a tracked file (`config/` and `.env`
  are git-ignored, everything else is not), never commit.
- **Go step by step and verify each step** with the command given before moving on.
  If a check fails, use the troubleshooting table; don't improvise workarounds that
  edit `src/`.
- A first run makes real network requests to Google News and publisher sites, and,
  if an LLM is configured, paid API calls. Say so before running it.

### 1. Check what's installed

```bash
python3 --version   # needs 3.13+
pipx --version      # for the recommended install
poetry --version    # only for a source/dev install
docker --version    # only for the Docker install
```

Report what's missing. Don't install anything yet.

### 2. Ask the user (one message, with defaults)

1. **Install mode**
   - **pipx (recommended for personal use):** a `digest` command on the PATH,
     config in `~/.config/newsfold/`, data in `~/.local/share/newsfold/`.
   - **Docker Compose:** for an always-on server, or for use alongside a Glance
     dashboard.
   - **Source checkout with Poetry:** only if they want to change the code.
2. **Topics:** what to follow. For each topic, a search keyword plus the languages to
   search in (`en`, `de`, `fr`, `es`, `vi` have region mappings built in; others
   fall back to region `US` unless added under `ingestion.region_by_language` in
   `settings.yaml`). Each one is `persistent` (followed indefinitely) or `temporary`
   (archive it manually when done).
3. **Reading language:** `target_language` in `settings.yaml`, default `en`. Every
   article gets translated into this.
4. **LLM provider:** ranking, translation and summaries need one, but the pipeline
   also runs without:
   - `none`: the digest still renders with original titles and excerpts.
   - `anthropic` (the default): needs `ANTHROPIC_API_KEY`.
   - `openai`: needs `OPENAI_API_KEY`.
   - `azure`: needs `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`, and the
     deployment name in `NEWSFOLD_LLM_MODEL`.
   - `ollama` / `lmstudio`: local, free, no key; needs the app running with a
     model pulled or loaded.
5. **Optional extras:**
   - a daily schedule;
   - the local web UI (`digest serve`);
   - sports widgets (football-data.org / bigballsdata.com free keys; F1 needs none).

If they have no preferences, use: pipx, the example topics replaced with one or two
of theirs, `en`, and either no LLM or whatever key they already have.

### 3. Install

**pipx** (from the clone, or straight from GitHub):

```bash
pipx install .                                       # in this folder
# or: pipx install git+https://github.com/2790karthikbhat/newsfold
digest init        # copies default config to ~/.config/newsfold/, creates the DB
```

**Source checkout (Poetry):** prefix every `digest` command below with `poetry run`;
config lives in `./config/`, data in `./data/` and `./out/`.

```bash
poetry install
poetry run digest init
```

**Docker Compose:**

```bash
docker network create dashboard          # once; docker-compose.yml expects it
cp .env.example .env
mkdir -p config && cp src/newsfold/defaults/*.yaml config/
docker compose up -d --build             # entrypoint runs `digest migrate`, then serves
```

Prefix the commands below with `docker compose exec newsfold`. Config is
`./config/` (mounted read-only), so edit it on the host, then run
`docker compose restart`. The full server guide is
[`docs/deploy-server.md`](docs/deploy-server.md).

**Verify:** `digest --help` lists the commands, and `digest init` printed the config
dir, database and digests paths. Use the paths it printed from here on.

### 4. Configure

Edit `<config dir>/topics.yaml`. Replace the examples; keep the shape:

```yaml
topics:
  - keyword: "electric vehicles"
    mode: persistent          # persistent | temporary
    languages: [en, de]       # one Google News query per language
```

LLM provider: put the key in `<config dir>/.env` (pipx), or `./.env` (source checkout
or Docker). `.env.example` lists every variable. To pick a non-default provider or
model, set `NEWSFOLD_LLM_PROVIDER` / `NEWSFOLD_LLM_MODEL` there; don't edit
`settings.yaml`'s `llm:` block. Model ids change over time: if the user names a
model, check the provider's current model list rather than guessing an id.

Then load the topics into the DB:

```bash
digest topics sync
digest topics list        # verify: shows exactly the user's topics
```

### 5. First run (small, then full)

```bash
digest run --dry-run --decode-limit 10   # fetch/decode for real, print planned LLM calls, spend nothing
digest run --decode-limit 30             # a quick real run
digest status                            # verify: articles by stage, latest run finished
digest render --open                     # opens the digest in the browser
```

`digest run` prints a one-line summary like `fetched 161 new · decoded 40 (0 broken)
· … · wrote out/digest-YYYY-MM-DD.html` and, with an LLM, the token usage and cost.
Without an LLM it says the rank/process stages were skipped. That's expected, not an
error. A full `digest run` without limits is fine once this works. The decode stage
is paced at about 1 request/second on purpose, so a first run with many topics takes
a few minutes.

### 6. Optional extras (only if asked)

- **Web UI:** `digest serve` → <http://127.0.0.1:8765>. It's local-only by default;
  don't bind it to `0.0.0.0` unless asked, because it has no auth.
- **Schedule:**
  - macOS/Linux cron: `0 7 * * * <full path to digest> run >> <data home>/cron.log 2>&1`
    (`which digest` gives the path).
  - Docker servers: `deploy/systemd/` has ready timers; their `WorkingDirectory`
    needs editing.
  - `digest cleanup` (retention) is never run automatically. Suggest it as a
    separate scheduled line, after the user has tried `digest cleanup --dry-run`.
- **Sports widgets:** add `NEWSFOLD_SPORTS_FOOTBALL_API_KEY` /
  `NEWSFOLD_SPORTS_CRICKET_API_KEY` to `.env`, set teams/competitions under `sports:`
  in `settings.yaml`, then run `digest sports fetch` and check with
  `digest sports list`.

### Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `LLMConfigError` / "rank/enrich skipped (…)" | No key for the selected provider. Add it to the right `.env` (step 4), or accept the no-LLM digest. |
| Many `broken` in `digest status` after decode | Google rate-limiting (429). Re-run with `--decode-concurrency 1`; don't lower `decode_request_interval_sec`. |
| "schema is behind" warning | Run `digest migrate`. |
| Ollama/LM Studio replies fail to parse | For LM Studio, keep `NEWSFOLD_LLM_JSON_FORMAT=text` (the default); for Ollama, try a larger model. |
| `docker compose up`: network `dashboard` not found | Run `docker network create dashboard` once. |
| Digest is empty | `digest status`: if nothing was fetched, check the topic keywords and `ingestion.lookback_days`; already-read articles are hidden (`digest render --include-read`). |
| `digest: command not found` after pipx | Run `pipx ensurepath`, then open a new shell. |

---

## Working on the code

- Architecture, layer rules and invariants: [`CLAUDE.md`](CLAUDE.md). For example,
  only `llm/client.complete_json` talks to a model, Google News links must be
  decoded before use, and runs must stay idempotent.
- Checks (CI runs the same):

  ```bash
  poetry install
  poetry run pre-commit run --all-files   # black, ruff, mypy --strict
  poetry run pytest
  ```

- Tests never hit the network or a real LLM; mock at the boundary, as the existing
  tests do.
- Schema changes go through a new Alembic migration in
  `src/newsfold/migrations/versions/`. Never edit an existing one.

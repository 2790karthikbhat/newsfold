# Running newsfold on a server (optionally alongside Glance)

Run the same single-user tool on an always-on machine (a home server, a NAS, a small
VPS) instead of a laptop. It's still one SQLite DB with no auth, so keep the web UI
on a private network or behind your own reverse proxy and auth.

## How it fits together

One Docker image plays two roles:

- the long-running `digest serve` process (web UI + the `/glance/*` widget
  endpoints), managed by `docker compose`;
- the target for one-shot pipeline jobs (`digest run`, `digest sports fetch`,
  `digest cleanup`) via `docker compose exec newsfold <command>`, triggered by
  systemd timers or cron.

`docker-compose.yml` joins an external Docker network called `dashboard`. If you also
run [Glance](https://github.com/glanceapp/glance) in Docker on the same host, put it
on that network too and it reaches newsfold by service name (`http://newsfold:8765`)
with no published ports. If you don't use Glance, the network is still needed
(create it once, as below); publish the port instead to reach the web UI.

## One-time setup

1. **Create the shared network** (once, before starting the compose project):
   ```bash
   docker network create dashboard
   ```

2. **Build and start newsfold** from this repo's directory on the server:
   ```bash
   cp .env.example .env   # then fill in your real keys/tokens
   mkdir -p config && cp src/newsfold/defaults/*.yaml config/   # edit config/topics.yaml
   docker compose up -d --build
   docker compose logs -f newsfold   # confirm it started cleanly; the
                                        # entrypoint runs `digest migrate`
                                        # before serving, so the schema is
                                        # ready on first boot
   docker compose exec newsfold digest topics sync   # load topics.yaml
   ```
   To open the web UI from other machines, uncomment the `ports:` block in
   `docker-compose.yml` and re-run `docker compose up -d`.

3. **(Glance only) Set `NEWSFOLD_PUBLIC_BASE_URL` in `.env` to how *your browser*
   reaches this server** — not `localhost`, not the Docker service name. E.g.:
   ```bash
   NEWSFOLD_PUBLIC_BASE_URL=http://<server-lan-ip-or-hostname>:8765
   ```
   This is only used to build the article links inside the `/glance/digest`
   widget fragment (`web/routes/glance.py`'s `_public_base_url`) — it's not
   how Glance *fetches* the fragment (that's the Docker service name below),
   it's what a browser clicks afterward. Getting this wrong produces dead
   links that "work" when tested with `curl` from the server but 404 in your
   actual browser — the exact bug this setting was added to fix.
   If you also uncommented the `ports:` block in `docker-compose.yml`, this
   same address is how you'd open the digest web UI directly, e.g.
   `http://<server-lan-ip-or-hostname>:8765/digest`.

4. **(Glance only) Update Glance's `docker-compose.yml`** (a separate compose project) to
   join the same network:
   ```yaml
   services:
     glance:
       # ...existing config...
       networks:
         - dashboard

   networks:
     dashboard:
       external: true
   ```

5. **(Glance only) Add the widgets to Glance's `config/home.yml`**, pointing
   at the Docker service name (it resolves once both containers are on the
   `dashboard` network; no port publishing needed):
   ```yaml
   - type: extension
     url: http://newsfold:8765/glance/digest
     allow-potentially-dangerous-html: true
     cache: 3m

   - type: group
     widgets:
       - type: extension
         url: http://newsfold:8765/glance/sports
         parameters: { sport: football }
         allow-potentially-dangerous-html: true
         cache: 30m
       # ...cricket, f1 the same way...
   ```

6. **(Glance only) Restart Glance** to pick up both changes:
   ```bash
   docker compose restart glance   # from Glance's own compose directory
   ```

7. **Install the systemd timers** for the periodic pipeline stages
   (`deploy/systemd/` in this repo — `digest run`/`digest cleanup` and
   `digest sports fetch` are one-shot jobs, deliberately *not* run inside the
   always-on container; see CLAUDE.md's "deliberate, not automatic" retention
   note):
   ```bash
   sudo cp deploy/systemd/newsfold-*.service deploy/systemd/newsfold-*.timer /etc/systemd/system/
   sudo $EDITOR /etc/systemd/system/newsfold-run.service      # fix WorkingDirectory
   sudo $EDITOR /etc/systemd/system/newsfold-sports.service   # fix WorkingDirectory
   sudo systemctl daemon-reload
   sudo systemctl enable --now newsfold-run.timer newsfold-sports.timer
   systemctl list-timers newsfold-*   # confirm both are scheduled
   ```

## Maintaining it

- **Logs**: `docker compose logs -f newsfold` (the web server);
  `journalctl -u newsfold-run.service` / `-u newsfold-sports.service`
  (the periodic jobs).
- **Updating code**: `git pull && docker compose up -d --build` — rebuilds
  the image, keeps `data/`/`out/` (bind-mounted, untouched by rebuilds). Any
  new Alembic revision in the pull applies automatically on the next
  container start (the entrypoint runs `digest migrate` before
  `digest serve` — a no-op if already current, so this is always safe to run).
- **Config**: `./config/*.yaml` is bind-mounted read-only; any file you don't
  provide falls back to the packaged default. Edit, then `docker compose restart`.
- **Backups**: `data/newsfold.db` is the entire state that matters — copy
  it (SQLite is a single file) while the container is stopped, or use
  `sqlite3 data/newsfold.db ".backup /path/to/backup.db"` for a live-safe
  copy without stopping anything.
- **Checking the pipeline ran**: `docker compose exec newsfold digest status`
  any time — same command you'd run locally.
- **One-off manual runs** (skip waiting for the timer):
  `docker compose exec newsfold digest run` /
  `docker compose exec newsfold digest sports fetch`.

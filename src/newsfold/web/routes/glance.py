"""Glance-dashboard `extension`-widget endpoints: bare HTML fragments (no
`<html>` wrapper, Glance's own CSS utility classes — not newsfold's own
Paper & Ink tokens, which only apply inside newsfold's own pages) plus the
`Widget-*` response headers Glance's extension protocol reads
(https://github.com/glanceapp/glance/blob/main/docs/extensions.md):
`Widget-Title` / `Widget-Title-URL` name the widget and its click target instead
of Glance's generic "Extension" default; `Widget-Content-Type: html` is set
explicitly rather than relying on the config-level `fallback-content-type`.

Read-only. `/glance/sports` never calls a sports API itself — it only formats
whatever `digest sports fetch` last cached (CLAUDE.md's `sports/` layer rule)."""

from __future__ import annotations

import calendar
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from newsfold.config import load_settings
from newsfold.db import session_scope
from newsfold.digest.render import DigestArticle, build_digest
from newsfold.sports.aggregate import SportsSnapshot, load_cached
from newsfold.sports.models import Event
from newsfold.timeutil import utcnow

_DIGEST_LIMIT = 8

# Tab labels for Glance's `group` widget — one `extension` sub-widget per sport,
# each hitting /glance/sports?sport=<key>.
_SPORT_TITLES = {"football": "Football", "cricket": "Cricket", "f1": "F1"}

# football/cricket get the "Last / Live / Next" card (glance_sports_card.html.j2,
# modeled on community-widgets/football-fixtures); F1 keeps the flat full-calendar
# list (glance_sports.html.j2) — that's what was explicitly asked for there.
_CARD_SPORTS = {"football", "cricket"}


def _digest_rank_key(article: DigestArticle) -> tuple[int, float]:
    rank = article.llm_rank if article.llm_rank is not None else 10_000
    return (rank, -(article.rank_score or 0.0))


def _epoch(dt: datetime | None) -> int | None:
    """Unix timestamp for Glance's `data-dynamic-relative-time` attribute (renders
    "3 days ago" client-side). `dt` is naive-UTC (newsfold.timeutil convention),
    so `calendar.timegm` — not `.timestamp()`, which assumes local time."""
    return calendar.timegm(dt.timetuple()) if dt else None


def _last_live_next(events: list[Event]) -> dict[str, Event | None]:
    """Split events into the 3 slots the card layout shows. `live` takes
    whatever the provider tagged "live" (at most one expected); `last` is the
    most recently finished match; `next` is the soonest still-scheduled one.
    Events with no `start_time` are ignored for last/next (can't be ordered)."""
    now = utcnow()
    live = next((e for e in events if e.status == "live"), None)
    finished = [
        e for e in events if e.status == "finished" and e.start_time and e.start_time <= now
    ]
    scheduled = [
        e for e in events if e.status == "scheduled" and e.start_time and e.start_time >= now
    ]
    last = max(finished, key=lambda e: e.start_time, default=None)  # type: ignore[arg-type,return-value]
    nxt = min(scheduled, key=lambda e: e.start_time, default=None)  # type: ignore[arg-type,return-value]
    return {"last_match": last, "live_match": live, "next_match": nxt}


def _public_base_url(request: Request) -> str:
    """The URL a *browser* should use to reach newsfold — never inferred from
    the incoming request's Host header. Glance's backend may reach this server
    over a hostname/network path (e.g. Docker's `host.docker.internal`, a
    container network, a reverse-proxy-internal address) that a browser can't
    resolve, so `request.base_url` is the wrong signal here even outside Docker
    — it reflects who fetched the fragment, not who will click the link.
    Configure `web.public_base_url` (or `$NEWSFOLD_PUBLIC_BASE_URL`) once it
    differs from `request.base_url`; that request-derived value is only a
    same-machine, no-Docker fallback."""
    configured = load_settings().section("web").get("public_base_url")
    if configured:
        return str(configured).rstrip("/")
    return str(request.base_url).rstrip("/")


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/glance")

    @router.get("/digest", response_class=HTMLResponse)
    def glance_digest(request: Request) -> HTMLResponse:
        with session_scope() as session:
            data = build_digest(session)
        articles = sorted(
            (a for topic in data.topics for a in topic.articles), key=_digest_rank_key
        )[:_DIGEST_LIMIT]
        base_url = _public_base_url(request)
        return templates.TemplateResponse(
            request,
            "glance_digest.html.j2",
            {"articles": articles, "base_url": base_url, "epoch": _epoch},
            headers={
                "Widget-Title": "Newsfold",
                "Widget-Title-URL": f"{base_url}/digest",
                "Widget-Content-Type": "html",
            },
        )

    @router.get("/sports", response_class=HTMLResponse)
    def glance_sports(request: Request, sport: str = "") -> HTMLResponse:
        snapshot = load_cached()
        if sport and snapshot is not None:
            snapshot = SportsSnapshot(
                events=[e for e in snapshot.events if e.sport == sport],
                fetched_at=snapshot.fetched_at,
                stale=snapshot.stale,
            )
        context: dict[str, object] = {
            "snapshot": snapshot,
            "epoch": _epoch,
            "sport_label": _SPORT_TITLES.get(sport, "sports"),
        }
        template_name = "glance_sports.html.j2"
        if sport in _CARD_SPORTS:
            template_name = "glance_sports_card.html.j2"
            context.update(_last_live_next(snapshot.events if snapshot else []))
        return templates.TemplateResponse(
            request,
            template_name,
            context,
            headers={
                "Widget-Title": _SPORT_TITLES.get(sport, "Sports"),
                "Widget-Content-Type": "html",
            },
        )

    return router

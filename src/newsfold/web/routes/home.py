"""Dashboard route: a quiet ledger line, top stories, and quick links."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from newsfold.db import session_scope
from newsfold.digest.render import build_digest
from newsfold.views import pipeline_status


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        with session_scope() as session:
            status = pipeline_status(session)
            data = build_digest(session)
        top_stories = [
            {"topic": t.keyword, "article": t.articles[0]} for t in data.topics if t.articles
        ]
        return templates.TemplateResponse(
            request, "home.html.j2", {"status": status, "top_stories": top_stories}
        )

    return router

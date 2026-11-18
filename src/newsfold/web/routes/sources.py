"""Source management routes: list, add, remove."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from newsfold.db import session_scope
from newsfold.models import Topic, TopicStatus
from newsfold.sources import SourceError, add_source, list_sources, remove_source


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/sources", response_class=HTMLResponse)
    def list_sources_route(
        request: Request, msg: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        with session_scope() as session:
            rows = list_sources(session)
            topic_by_id = {t.id: t.keyword for t in session.scalars(select(Topic)).all()}
            active_topics = [
                t.keyword
                for t in session.scalars(
                    select(Topic).where(Topic.status == TopicStatus.active).order_by(Topic.id)
                ).all()
            ]
            sources = [
                {
                    "id": s.id,
                    "name": s.name,
                    "status": s.status.value,
                    "input_url": s.input_url,
                    "resolved_feed_url": s.resolved_feed_url,
                    "scope": (
                        topic_by_id.get(s.topic_id, "all topics") if s.topic_id else "all topics"
                    ),
                }
                for s in rows
            ]
        return templates.TemplateResponse(
            request,
            "sources.html.j2",
            {"sources": sources, "topics": active_topics, "msg": msg, "error": error},
        )

    @router.post("/sources")
    def create_source(
        url: str = Form(...),
        name: str = Form(""),
        topic: str = Form(""),
    ) -> RedirectResponse:
        try:
            with session_scope() as session:
                result = add_source(session, url, name=name or None, topic_keyword=topic or None)
            if result.source.status.value == "active":
                query = urlencode({"msg": f"added {result.source.name}"})
            else:
                query = urlencode({"error": f"added but broken: {result.discovery.error}"})
        except SourceError as exc:
            query = urlencode({"error": str(exc)})
        return RedirectResponse(url=f"/sources?{query}", status_code=303)

    @router.post("/sources/{source_id}/remove")
    def remove_source_route(source_id: int) -> RedirectResponse:
        with session_scope() as session:
            ok = remove_source(session, source_id)
        param, msg = ("msg", "removed") if ok else ("error", f"no source with id {source_id}")
        return RedirectResponse(url=f"/sources?{urlencode({param: msg})}", status_code=303)

    return router

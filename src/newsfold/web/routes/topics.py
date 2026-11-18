"""Topic management routes: list, add, archive."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from newsfold.db import session_scope
from newsfold.models import Topic
from newsfold.topics import TopicError, add_topic, archive_topic


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/topics", response_class=HTMLResponse)
    def list_topics(
        request: Request, msg: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        with session_scope() as session:
            rows = session.scalars(select(Topic).order_by(Topic.id)).all()
            topics = [
                {
                    "id": t.id,
                    "keyword": t.keyword,
                    "mode": t.mode.value,
                    "status": t.status.value,
                    "languages": ", ".join(json.loads(t.languages)),
                }
                for t in rows
            ]
        return templates.TemplateResponse(
            request, "topics.html.j2", {"topics": topics, "msg": msg, "error": error}
        )

    @router.post("/topics")
    def create_topic(
        keyword: str = Form(...),
        mode: str = Form("temporary"),
        languages: str = Form("en"),
    ) -> RedirectResponse:
        langs = [lang.strip() for lang in languages.split(",") if lang.strip()]
        try:
            with session_scope() as session:
                topic = add_topic(session, keyword, mode=mode, languages=langs)
            query = urlencode({"msg": f"added {topic.keyword}"})
        except TopicError as exc:
            query = urlencode({"error": str(exc)})
        return RedirectResponse(url=f"/topics?{query}", status_code=303)

    @router.post("/topics/{topic_id}/archive")
    def archive_topic_route(topic_id: int) -> RedirectResponse:
        with session_scope() as session:
            ok = archive_topic(session, topic_id)
        param, msg = ("msg", "archived") if ok else ("error", f"no topic with id {topic_id}")
        return RedirectResponse(url=f"/topics?{urlencode({param: msg})}", status_code=303)

    return router

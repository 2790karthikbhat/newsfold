"""Digest browsing: live topic overview, per-topic articles, article detail,
reader-state actions (read/archive/save/delete), the saved (read-later) list,
and the archive of past `digest render` files."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from newsfold.articles import archive, delete, mark_read, mark_unread, save, unarchive, unsave
from newsfold.db import session_scope
from newsfold.digest.render import DigestArticle, DigestData, DigestTopic, build_digest
from newsfold.views import list_articles
from newsfold.web.digest_history import list_digest_files, read_digest_file


def _find_topic(data: DigestData, keyword: str) -> DigestTopic | None:
    for topic in data.topics:
        if topic.keyword.lower() == keyword.lower():
            return topic
    return None


def _find_article(data: DigestData, article_id: int) -> tuple[DigestTopic, DigestArticle] | None:
    for topic in data.topics:
        for article in topic.articles:
            if article.id == article_id:
                return topic, article
    return None


def _safe_next(next_url: str | None, default: str) -> str:
    """Only ever redirect within this app — `next` comes from a form field."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return default


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/digest")

    @router.get("", response_class=HTMLResponse)
    def index(request: Request, msg: str | None = None, error: str | None = None) -> HTMLResponse:
        with session_scope() as session:
            data = build_digest(session)
        topics = [
            {
                "keyword": t.keyword,
                "count": len(t.articles),
                "top": t.articles[0] if t.articles else None,
            }
            for t in data.topics
        ]
        return templates.TemplateResponse(
            request, "digest_index.html.j2", {"topics": topics, "msg": msg, "error": error}
        )

    @router.get("/saved", response_class=HTMLResponse)
    def saved_view(
        request: Request, msg: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        with session_scope() as session:
            rows = list_articles(session, saved=True, limit=200)
        return templates.TemplateResponse(
            request, "digest_saved.html.j2", {"articles": rows, "msg": msg, "error": error}
        )

    @router.get("/topic/{keyword}", response_class=HTMLResponse)
    def topic_view(
        request: Request, keyword: str, msg: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        with session_scope() as session:
            data = build_digest(session)
        topic = _find_topic(data, keyword)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"no live articles for {keyword!r}")
        return templates.TemplateResponse(
            request, "digest_topic.html.j2", {"topic": topic, "msg": msg, "error": error}
        )

    @router.get("/article/{article_id}", response_class=HTMLResponse)
    def article_view(
        request: Request, article_id: int, msg: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        with session_scope() as session:
            # include_read=True: reachable regardless of read_state (e.g. from the
            # Saved list, or right after you just marked/archived it on this page).
            data = build_digest(session, include_read=True)
        found = _find_article(data, article_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"no live article with id {article_id}")
        topic, article = found
        return templates.TemplateResponse(
            request,
            "digest_article.html.j2",
            {"topic": topic, "article": article, "msg": msg, "error": error},
        )

    def _action(
        article_id: int,
        fn: Callable[[Session, list[int]], int],
        next_url: str | None,
        *,
        default: str,
        verb: str,
    ) -> RedirectResponse:
        with session_scope() as session:
            n = fn(session, [article_id])
        dest = _safe_next(next_url, default)
        msg = verb if n else f"no article with id {article_id}"
        param = "msg" if n else "error"
        sep = "&" if "?" in dest else "?"
        return RedirectResponse(url=f"{dest}{sep}{urlencode({param: msg})}", status_code=303)

    @router.post("/article/{article_id}/read")
    def read_route(article_id: int, next: str = Form("")) -> RedirectResponse:
        return _action(article_id, mark_read, next, default="/digest", verb="marked read")

    @router.post("/article/{article_id}/unread")
    def unread_route(article_id: int, next: str = Form("")) -> RedirectResponse:
        return _action(article_id, mark_unread, next, default="/digest", verb="marked unread")

    @router.post("/article/{article_id}/archive")
    def archive_route(article_id: int, next: str = Form("")) -> RedirectResponse:
        return _action(article_id, archive, next, default="/digest", verb="archived")

    @router.post("/article/{article_id}/unarchive")
    def unarchive_route(article_id: int, next: str = Form("")) -> RedirectResponse:
        return _action(article_id, unarchive, next, default="/digest", verb="unarchived")

    @router.post("/article/{article_id}/save")
    def save_route(article_id: int, next: str = Form("")) -> RedirectResponse:
        return _action(article_id, save, next, default="/digest", verb="saved for later")

    @router.post("/article/{article_id}/unsave")
    def unsave_route(article_id: int, next: str = Form("")) -> RedirectResponse:
        return _action(article_id, unsave, next, default="/digest/saved", verb="unsaved")

    @router.post("/article/{article_id}/delete")
    def delete_route(article_id: int, next: str = Form("")) -> RedirectResponse:
        return _action(article_id, delete, next, default="/digest", verb="deleted")

    @router.get("/archive", response_class=HTMLResponse)
    def archive_view(request: Request) -> HTMLResponse:
        files = list_digest_files()
        return templates.TemplateResponse(request, "digest_archive.html.j2", {"files": files})

    @router.get("/archive/{run_date}")
    def archive_file(run_date: str, format: str = "html") -> Response:
        content = read_digest_file(run_date, fmt=format)
        if content is None:
            raise HTTPException(status_code=404, detail=f"no digest for {run_date} ({format})")
        if format == "md":
            return PlainTextResponse(content, media_type="text/markdown")
        return HTMLResponse(content)

    @router.get("/latest")
    def latest_redirect() -> RedirectResponse:
        return RedirectResponse(url="/digest", status_code=307)

    return router

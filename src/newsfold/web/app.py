"""The only place the local web UI's FastAPI app is constructed (CLAUDE.md layer rule).

Route handlers parse the request, call one function in `topics.py`/`sources.py`
(or `digest/render.py` read-only), and redirect — no business logic here, same
rule as `cli.py`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"


def create_app() -> FastAPI:
    from newsfold.web.routes import digest, glance, home, sources, topics

    app = FastAPI(title="Newsfold")
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    app.include_router(home.build_router(templates))
    app.include_router(digest.build_router(templates))
    app.include_router(topics.build_router(templates))
    app.include_router(sources.build_router(templates))
    app.include_router(glance.build_router(templates))
    return app


def run_server(*, host: str = "127.0.0.1", port: int = 8765, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run(
        "newsfold.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )

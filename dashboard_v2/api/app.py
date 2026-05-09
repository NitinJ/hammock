"""FastAPI factory for Hammock v2 dashboard."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard_v2.api import jobs, projects, sse, workflows
from dashboard_v2.settings import load_settings

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="Hammock v2", version="0.1.0")

    # Permissive CORS during development; tighten if needed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(workflows.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(sse.router, prefix="/sse")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"ok": "true", "version": "v2", "runner_mode": settings.runner_mode}

    # Serve the SPA from frontend/dist if it's been built.
    if settings.static_dist.is_dir():
        assets = settings.static_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="spa-assets")
        index_html = settings.static_dist / "index.html"

        @app.get("/")
        @app.get("/{path:path}")
        def serve_spa(path: str | None = None) -> FileResponse:
            return FileResponse(str(index_html))

    return app


app = create_app()

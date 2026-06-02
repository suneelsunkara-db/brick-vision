"""FastAPI application factory for the Console API sidecar (v0.7.7 MVI).

Boots the three read routers (health, identity, knowledge). CORS is
locked down to the Vite dev origin (and any explicit allowlist supplied
via ``CONSOLE_API_CORS_ALLOW_ORIGINS``). When ``CONSOLE_API_SPA_DIST_DIR``
is set, the sidecar additionally serves the built SPA from the same
process so a single ASGI app can host the whole console.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .routers import health, identity, knowledge
from .settings import get_settings


def create_app() -> FastAPI:
    """Construct the FastAPI instance + wire every router."""

    settings = get_settings()
    app = FastAPI(
        title="BrickVision Console API",
        version=__version__,
        description=(
            "FastAPI sidecar for the BrickVision Knowledge UI. Reads from"
            " the Lakebase Autoscaling Synced Tables produced by the"
            " capability indexer Job. See"
            " docs/23-databricks-capability-graph.md."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(identity.router)
    app.include_router(knowledge.router)

    # Uniform error envelope so the SPA's ``ApiError`` always
    # decodes a structured ``{ reason_code, message, ... }`` body.
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            body = detail
        else:
            body = {"reason_code": "HTTP_ERROR", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "reason_code": "VALIDATION_ERROR",
                "message": "Request payload failed validation.",
                "errors": exc.errors(),
            },
        )

    @app.on_event("startup")
    async def _warm_lakebase() -> None:
        """Pre-establish the persistent Lakebase connection at startup."""
        import asyncio
        from console_api.lakebase import lakebase_configured, lakebase_connection

        if lakebase_configured():

            def _connect() -> None:
                try:
                    with lakebase_connection() as conn:
                        conn.execute("SELECT 1")
                except Exception:
                    pass

            asyncio.get_event_loop().run_in_executor(None, _connect)

    if settings.spa_dist_dir:
        _mount_spa(app, settings.spa_dist_dir)

    return app


def _mount_spa(app: FastAPI, dist_dir: str) -> None:
    """When invoked from a packaged install, serve the SPA too."""

    dist_path = Path(dist_dir)
    if not dist_path.exists():
        return

    assets_dir = dist_path / "assets"
    if assets_dir.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=assets_dir, check_dir=True),
            name="assets",
        )

    index_html = dist_path / "index.html"
    if not index_html.exists():
        return

    # SPA fallback — every non-API path returns index.html so
    # TanStack Router can take over client-side.
    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "assets/")):
            # FastAPI never reaches us for these because of route
            # ordering, but be defensive.
            return FileResponse(index_html)
        return FileResponse(index_html)


app = create_app()


__all__ = ["app", "create_app"]


if os.environ.get("CONSOLE_API_PRINT_BANNER", "1") == "1":  # pragma: no cover
    print(f"console_api {__version__} ready")

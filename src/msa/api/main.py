"""FastAPI app. Run with `python -m msa.cli api` (or `uvicorn msa.api.main:app`)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from msa.api.routes_platform import router as platform_router
from msa.core.config import get_settings
from msa.core.db import init_db
from msa.core.plugins import load_plugins, loaded_plugins
from msa.core.registry import registry
from msa.core.services import ServiceError


def create_app(start_scheduler: bool | None = None) -> FastAPI:
    load_plugins()
    settings = get_settings()
    if start_scheduler is None:
        start_scheduler = settings.scheduler

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        if start_scheduler:
            from msa.core import scheduler

            scheduler.start(settings.daily_hour)
        yield
        if start_scheduler:
            from msa.core import scheduler

            scheduler.stop()

    app = FastAPI(title="Makerspace Safety Agent", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ServiceError)
    async def _service_error(_req: Request, exc: ServiceError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health", tags=["meta"])
    def health():
        return {"ok": True, "plugins": loaded_plugins, "llm": settings.llm_provider}

    app.include_router(platform_router)
    for r in registry.routers:
        app.include_router(r)
    return app


app = create_app()

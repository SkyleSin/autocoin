"""FastAPI 앱 — DB 세션·`/jobs` CRUD·백그라운드 잡 워커."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from routers.jobs import router as jobs_router
from utils.config import Settings
from utils.logging_setup import setup_logging
from worker.runner import spawn_worker_tasks
from worker.settings import WorkerSettings

_logging_configured = False

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    global _logging_configured
    settings = settings or Settings()
    if not _logging_configured:
        setup_logging(settings=settings)
        _logging_configured = True
    if not settings.database_url.strip():
        msg = "DATABASE_URL이 필요합니다."
        raise ValueError(msg)

    engine = create_async_engine(settings.database_url.strip())
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.session_factory = session_factory
        w_settings = WorkerSettings()
        _queue, worker_tasks = spawn_worker_tasks(session_factory, settings, w_settings)
        app.state.worker_tasks = worker_tasks
        app.state.worker_queue = _queue
        yield
        for t in worker_tasks:
            t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        logger.info("job workers stopped")
        await engine.dispose()

    app = FastAPI(title="autocoin", version="0.1.0", lifespan=lifespan)
    app.include_router(jobs_router)
    return app


settings = Settings()
app = create_app(settings)


def main() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )


if __name__ == "__main__":
    main()

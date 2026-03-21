from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import autocoin.db.models  # noqa: F401 — 메타데이터에 모델 등록
from autocoin.api.routes import health, signals
from autocoin.config import Settings
from autocoin.db.base import Base


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.database_url:
        msg = (
            "DATABASE_URL이 필요합니다. 예: postgresql+asyncpg://user:***REMOVED***@localhost:5432/autocoin"
        )
        raise ValueError(msg)

    engine = create_async_engine(
        settings.database_url,
        echo=settings.db_echo_sql,
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app.state.settings = settings
        app.state.session_factory = session_factory
        yield
        await engine.dispose()

    app = FastAPI(title="autocoin", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(signals.router)
    return app


app = create_app()

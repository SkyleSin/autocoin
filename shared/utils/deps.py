from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from services.job_service import JobService


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_job_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobService:
    return JobService(db)

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from autocoin.api.deps import get_db
from autocoin.services.health_service import HealthService

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
async def health_db(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    await HealthService(db).check_database()
    return {"status": "ok", "database": "reachable"}

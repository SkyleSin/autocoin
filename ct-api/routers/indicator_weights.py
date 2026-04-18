"""지표 가중치 라우터 — 조회 / upsert / count."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.indicator_weight import IndicatorWeight
from utils.deps import get_db

router = APIRouter(prefix="/api/indicator-weights", tags=["indicator-weights"])


@router.get("")
async def list_weights(
    db: Annotated[AsyncSession, Depends(get_db)],
    timeframe: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> list[dict]:
    stmt = select(IndicatorWeight)
    if timeframe:
        stmt = stmt.where(IndicatorWeight.timeframe == timeframe)
    if is_active is not None:
        stmt = stmt.where(IndicatorWeight.is_active == is_active)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "name": r.name,
            "direction": r.direction,
            "timeframe": r.timeframe,
            "accuracy": r.accuracy,
            "sample_count": r.sample_count,
            "hit_count": r.hit_count,
            "is_active": r.is_active,
        }
        for r in rows
    ]


class UpsertBody(BaseModel):
    name: str
    direction: str
    timeframe: str
    accuracy: float
    sample_count: int = 0
    hit_count: int = 0
    is_active: bool = True


@router.post("/upsert")
async def upsert(
    body: UpsertBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = (await db.execute(
        select(IndicatorWeight).where(
            IndicatorWeight.name == body.name,
            IndicatorWeight.direction == body.direction,
            IndicatorWeight.timeframe == body.timeframe,
        )
    )).scalar_one_or_none()

    if row:
        row.accuracy = body.accuracy
        row.sample_count = body.sample_count
        row.hit_count = body.hit_count
        row.is_active = body.is_active
        action = "updated"
    else:
        db.add(IndicatorWeight(
            name=body.name,
            direction=body.direction,
            timeframe=body.timeframe,
            accuracy=body.accuracy,
            sample_count=body.sample_count,
            hit_count=body.hit_count,
            is_active=body.is_active,
        ))
        action = "created"

    await db.commit()
    return {"action": action, "name": body.name}


@router.get("/count")
async def count(
    db: Annotated[AsyncSession, Depends(get_db)],
    timeframe: Annotated[str | None, Query()] = None,
) -> dict:
    stmt = select(func.count()).select_from(IndicatorWeight)
    if timeframe:
        stmt = stmt.where(IndicatorWeight.timeframe == timeframe)
    total = (await db.execute(stmt)).scalar() or 0
    return {"count": total}

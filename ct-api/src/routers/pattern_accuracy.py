"""패턴 적중률 라우터 — upsert / summary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.pattern_accuracy import PatternAccuracy
from services.accuracy_service import get_accuracy_summary
from utils.deps import get_db

router = APIRouter(prefix="/api/pattern-accuracy", tags=["pattern-accuracy"])


class UpsertBody(BaseModel):
    pattern_name: str
    total_count: int
    hit_count: int
    accuracy_pct: float


@router.post("/upsert")
async def upsert(
    body: UpsertBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = (await db.execute(
        select(PatternAccuracy).where(
            PatternAccuracy.pattern_name == body.pattern_name,
        )
    )).scalar_one_or_none()

    if row:
        row.total_count = body.total_count
        row.hit_count = body.hit_count
        row.accuracy_pct = body.accuracy_pct
        row.last_updated_at = datetime.now(UTC)
        action = "updated"
    else:
        db.add(PatternAccuracy(
            pattern_name=body.pattern_name,
            total_count=body.total_count,
            hit_count=body.hit_count,
            accuracy_pct=body.accuracy_pct,
            last_updated_at=datetime.now(UTC),
        ))
        action = "created"

    await db.commit()
    return {"action": action, "pattern_name": body.pattern_name}


@router.get("/summary")
async def summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    include_inactive: Annotated[bool, Query()] = False,
) -> list[dict]:
    return await get_accuracy_summary(db, include_inactive=include_inactive)

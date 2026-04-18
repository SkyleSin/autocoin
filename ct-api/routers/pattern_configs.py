"""패턴 설정 라우터 — 조회 / 수정."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.pattern_config import PatternConfig
from services.pattern_service import invalidate_cache
from utils.deps import get_db

router = APIRouter(prefix="/api/pattern-configs", tags=["pattern-configs"])


@router.get("")
async def list_configs(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    rows = (await db.execute(select(PatternConfig))).scalars().all()
    return [
        {
            "name": r.name,
            "name_ko": r.name_ko,
            "signal": r.signal,
            "active": r.active,
            "eval_days": r.eval_days,
            "accuracy_pct": r.accuracy_pct,
            "total_count": r.total_count,
            "hit_count": r.hit_count,
            "buy_allocation_pct": r.buy_allocation_pct,
            "sell_recommendation_pct": r.sell_recommendation_pct,
        }
        for r in rows
    ]


class PatchBody(BaseModel):
    accuracy_pct: float | None = None
    total_count: int | None = None
    hit_count: int | None = None


@router.patch("/{name}")
async def patch_config(
    name: str,
    body: PatchBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = (await db.execute(
        select(PatternConfig).where(PatternConfig.name == name)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="pattern not found")

    if body.accuracy_pct is not None:
        row.accuracy_pct = body.accuracy_pct
    if body.total_count is not None:
        row.total_count = body.total_count
    if body.hit_count is not None:
        row.hit_count = body.hit_count

    await db.commit()
    invalidate_cache()
    return {"name": row.name, "updated": True}

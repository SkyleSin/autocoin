"""패턴 판정 기록 라우터 — 생성 / 통계."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.pattern_evaluation import PatternEvaluation
from utils.deps import get_db

router = APIRouter(prefix="/api/pattern-evaluations", tags=["pattern-evaluations"])


class CreateBody(BaseModel):
    pattern_name: str
    signal_type: str
    market: str
    signal_date: datetime
    signal_price: float
    eval_date: datetime
    eval_price: float
    pct_change: float
    hit: bool
    signal_id: UUID | None = None


@router.post("")
async def create(
    body: CreateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    row = PatternEvaluation(
        pattern_name=body.pattern_name,
        signal_type=body.signal_type,
        market=body.market,
        signal_date=body.signal_date,
        signal_price=body.signal_price,
        eval_date=body.eval_date,
        eval_price=body.eval_price,
        pct_change=body.pct_change,
        hit=body.hit,
        signal_id=body.signal_id,
    )
    db.add(row)
    await db.commit()
    return {"id": str(row.id), "created": True}


@router.get("/stats")
async def stats(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    rows = (await db.execute(
        select(
            PatternEvaluation.pattern_name,
            func.count().label("total"),
            func.count().filter(PatternEvaluation.hit.is_(True)).label("hits"),
        ).group_by(PatternEvaluation.pattern_name)
        .order_by(func.count().desc())
    )).all()
    return [
        {
            "pattern_name": name,
            "total": total,
            "hits": hits,
            "accuracy_pct": round(hits / total * 100, 1) if total > 0 else 0.0,
        }
        for name, total, hits in rows
    ]

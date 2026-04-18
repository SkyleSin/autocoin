"""섀도우 트레이딩 라우터 — 가상 매매 기록 / 요약."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.shadow_service import get_shadow_summary, record_shadow_trade
from utils.deps import get_db

router = APIRouter(prefix="/api/shadow", tags=["shadow"])


class TradeBody(BaseModel):
    market: str
    signal_type: str
    close_price: float
    confidence: float
    signal_id: UUID | None = None


@router.post("/trade")
async def trade(
    body: TradeBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await record_shadow_trade(
        db,
        market=body.market,
        signal_type=body.signal_type,
        close_price=body.close_price,
        confidence=body.confidence,
        signal_id=body.signal_id,
    )
    if result is None:
        return {"recorded": False, "reason": "조건 미충족"}
    return {
        "recorded": True,
        "id": str(result.id),
        "side": result.side,
        "amount_krw": float(result.amount_krw),
    }


@router.get("/summary")
async def summary(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    return await get_shadow_summary(db)

"""활성 알림 관리 라우터 — check / pass / acted / delete."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from models.active_alert import ActiveAlert
from pydantic import BaseModel
from services.alert_service import check_alert, mark_passed
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.deps import get_db

router = APIRouter(prefix="/api/active-alerts", tags=["active-alerts"])


class CheckBody(BaseModel):
    market: str
    timeframe: str
    signal_type: str
    confidence: float
    reasons: list[str] = []
    indicators: dict | None = None
    remind_interval_sec: float = 600.0
    user_id: int | None = None


class MarketBody(BaseModel):
    market: str
    user_id: int | None = None


@router.post("/check")
async def check(
    body: CheckBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    action = await check_alert(
        db,
        body.market,
        body.timeframe,
        body.signal_type,
        body.confidence,
        body.reasons,
        body.indicators,
        user_id=body.user_id,
        remind_interval_sec=body.remind_interval_sec,
    )
    await db.commit()
    return {"action": action}


@router.post("/mark-passed")
async def passed(
    body: MarketBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    ok = await mark_passed(db, body.market, user_id=body.user_id)
    await db.commit()
    return {"ok": ok}


@router.patch("/mark-acted")
async def acted(
    body: MarketBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from datetime import UTC, datetime

    stmt = select(ActiveAlert).where(ActiveAlert.market == body.market)
    if body.user_id is not None:
        stmt = stmt.where(ActiveAlert.user_id == body.user_id)
    rows = (await db.execute(stmt)).scalars().all()
    updated = 0
    for row in rows:
        row.acted_at = datetime.now(UTC)
        updated += 1
    await db.commit()
    return {"updated": updated}


@router.delete("")
async def remove(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: Annotated[str, Query()],
    timeframe: Annotated[str, Query()],
) -> dict:
    result = await db.execute(
        delete(ActiveAlert).where(
            ActiveAlert.market == market,
            ActiveAlert.timeframe == timeframe,
        )
    )
    await db.commit()
    return {"deleted": result.rowcount}

"""활성 알림 액션 라우터 — mark-passed, mark-acted, delete."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from models.active_alert import ActiveAlert
from routers.active_alerts_schema import MarketBody
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.alert_logic import pass_fingerprint
from utils.deps import get_db

actions_router = APIRouter()


@actions_router.post("/mark-passed")
async def passed(
    body: MarketBody, db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    stmt = select(ActiveAlert).where(ActiveAlert.market == body.market)
    if body.user_id is not None:
        stmt = stmt.where(ActiveAlert.user_id == body.user_id)
    results = (await db.execute(stmt)).scalars().all()
    if not results:
        return {"ok": False}
    now = datetime.now(UTC)
    for alert in results:
        alert.passed_at = now
        alert.passed_reasons = pass_fingerprint(alert.indicators, alert.confidence)
    await db.commit()
    return {"ok": True}


@actions_router.patch("/mark-acted")
async def acted(
    body: MarketBody, db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
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


@actions_router.delete("")
async def remove(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: Annotated[str, Query()],
    timeframe: Annotated[str, Query()],
) -> dict:
    result = await db.execute(
        delete(ActiveAlert).where(
            ActiveAlert.market == market, ActiveAlert.timeframe == timeframe,
        )
    )
    await db.commit()
    return {"deleted": result.rowcount}

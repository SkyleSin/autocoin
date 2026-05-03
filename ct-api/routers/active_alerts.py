"""활성 알림 CRUD 라우터 — 순수 DB 게이트웨이."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from models.active_alert import ActiveAlert
from routers.active_alerts_actions import actions_router
from routers.active_alerts_schema import CreateBody, UpdateBody, to_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.deps import get_db

router = APIRouter(prefix="/api/active-alerts", tags=["active-alerts"])
router.include_router(actions_router)

_DT_FIELDS = frozenset({"last_alerted_at", "acted_at", "passed_at"})


@router.get("/find")
async def find_alert(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: Annotated[str, Query()],
    timeframe: Annotated[str, Query()],
    user_id: Annotated[int | None, Query()] = None,
) -> dict | None:
    stmt = select(ActiveAlert).where(
        ActiveAlert.market == market, ActiveAlert.timeframe == timeframe,
    )
    if user_id is not None:
        stmt = stmt.where(ActiveAlert.user_id == user_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    return to_dict(row) if row else None


@router.post("")
async def create_alert(
    body: CreateBody, db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    last_alerted = (
        datetime.fromisoformat(body.last_alerted_at)
        if body.last_alerted_at else datetime.now(UTC)
    )
    row = ActiveAlert(
        user_id=body.user_id, market=body.market, timeframe=body.timeframe,
        signal_type=body.signal_type, confidence=body.confidence,
        reasons=body.reasons, indicators=body.indicators,
        last_alerted_at=last_alerted,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return to_dict(row)


@router.patch("/update")
async def update_alert_fields(
    body: UpdateBody, db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    stmt = select(ActiveAlert).where(
        ActiveAlert.market == body.market, ActiveAlert.timeframe == body.timeframe,
    )
    if body.user_id is not None:
        stmt = stmt.where(ActiveAlert.user_id == body.user_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return {"updated": 0}
    for key, value in body.updates.items():
        if key.startswith("_"):
            continue
        if key in _DT_FIELDS and isinstance(value, str):
            value = datetime.fromisoformat(value)
        setattr(row, key, value)
    await db.commit()
    return {"updated": 1}

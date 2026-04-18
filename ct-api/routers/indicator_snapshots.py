"""IndicatorSnapshot 라우터."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.indicator_snapshot import IndicatorSnapshot
from utils.deps import get_db

router = APIRouter(prefix="/api/indicator-snapshots", tags=["indicator-snapshots"])


class SnapshotUpsert(BaseModel):
    market: str
    timeframe: str
    candle_open_at: datetime
    indicators: dict[str, Any]


@router.post("/upsert", status_code=status.HTTP_200_OK)
async def upsert(
    body: SnapshotUpsert,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    stmt = pg_insert(IndicatorSnapshot).values(
        market=body.market,
        timeframe=body.timeframe,
        candle_open_at=body.candle_open_at,
        indicators=body.indicators,
    ).on_conflict_do_update(
        constraint="uq_indicator_snapshot_market_tf_candle",
        set_={"indicators": body.indicators},
    )
    await db.execute(stmt)
    await db.commit()
    return {"status": "ok"}


@router.get("/latest")
async def latest(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict[str, Any] | None:
    row = (await db.execute(
        select(IndicatorSnapshot).where(
            IndicatorSnapshot.market == market,
            IndicatorSnapshot.timeframe == timeframe,
        ).order_by(IndicatorSnapshot.candle_open_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not row:
        return None
    return {
        "id": str(row.id),
        "market": row.market,
        "timeframe": row.timeframe,
        "candle_open_at": row.candle_open_at.isoformat(),
        "indicators": row.indicators,
        "created_at": row.created_at.isoformat(),
    }

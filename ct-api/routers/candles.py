"""Candle CRUD 라우터."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.candle import Candle
from utils.deps import get_db

router = APIRouter(prefix="/api/candles", tags=["candles"])


class CandleItem(BaseModel):
    market: str
    timeframe: str
    open_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class CandleUpsertBody(BaseModel):
    candles: list[CandleItem]


@router.post("/upsert", status_code=status.HTTP_200_OK)
async def upsert(
    body: CandleUpsertBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, int]:
    if not body.candles:
        return {"upserted": 0}
    for c in body.candles:
        stmt = pg_insert(Candle).values(
            market=c.market, timeframe=c.timeframe, open_at=c.open_at,
            open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
        ).on_conflict_do_update(
            constraint="uq_candles_market_tf_open",
            set_={"open": c.open, "high": c.high, "low": c.low,
                   "close": c.close, "volume": c.volume},
        )
        await db.execute(stmt)
    await db.commit()
    return {"upserted": len(body.candles)}


@router.get("")
async def list_candles(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: str = Query(...),
    timeframe: str = Query(...),
    limit: int = Query(200, ge=1, le=1000),
    order: str = Query("desc"),
) -> list[dict[str, Any]]:
    q = select(Candle).where(
        Candle.market == market, Candle.timeframe == timeframe,
    )
    q = q.order_by(Candle.open_at.desc() if order == "desc" else Candle.open_at.asc())
    rows = (await db.execute(q.limit(limit))).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/count")
async def count(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict[str, int]:
    cnt = (await db.execute(
        select(func.count()).select_from(Candle).where(
            Candle.market == market, Candle.timeframe == timeframe,
        )
    )).scalar() or 0
    return {"count": cnt}


def _to_dict(r: Candle) -> dict[str, Any]:
    return {
        "id": str(r.id), "market": r.market, "timeframe": r.timeframe,
        "open_at": r.open_at.isoformat(),
        "open": str(r.open), "high": str(r.high), "low": str(r.low),
        "close": str(r.close), "volume": str(r.volume),
    }

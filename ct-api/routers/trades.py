"""Trade CRUD 라우터."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.trade import Trade
from utils.deps import get_db

router = APIRouter(prefix="/api/trades", tags=["trades"])


class TradeCreate(BaseModel):
    market: str
    side: str
    volume: Decimal
    price_krw: Decimal | None = None
    status: str = "unknown"
    exchange_order_id: str | None = None
    executed_at: datetime | None = None
    notes: str | None = None
    payload: dict[str, Any] | None = None
    user_id: int | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: TradeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = Trade(**body.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


@router.get("")
async def list_trades(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(10, ge=1, le=200),
    order: str = Query("desc"),
) -> list[dict[str, Any]]:
    q = select(Trade).order_by(
        Trade.created_at.desc() if order == "desc" else Trade.created_at.asc(),
    ).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/daily-count")
async def daily_count(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, int]:
    today_start = datetime.combine(
        date.today(), datetime.min.time(), tzinfo=UTC,
    )
    cnt = (await db.execute(
        select(func.count()).select_from(Trade).where(
            Trade.created_at >= today_start,
        )
    )).scalar() or 0
    return {"count": cnt}


def _to_dict(r: Trade) -> dict[str, Any]:
    return {
        "id": str(r.id), "user_id": r.user_id,
        "market": r.market, "side": r.side,
        "volume": str(r.volume),
        "price_krw": str(r.price_krw) if r.price_krw else None,
        "status": r.status,
        "exchange_order_id": r.exchange_order_id,
        "executed_at": r.executed_at.isoformat() if r.executed_at else None,
        "notes": r.notes,
        "created_at": r.created_at.isoformat(),
    }

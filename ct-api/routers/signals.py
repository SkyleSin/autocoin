"""Signal CRUD 라우터."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.signal import Signal
from routers.signals_helpers import _to_dict
from routers.signals_schemas import CloseOppositeBody, SignalCreate, SignalUpdate
from utils.deps import get_db

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: SignalCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = Signal(**body.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


@router.get("/latest")
async def latest(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: str = Query(...),
    limit: int = Query(1, ge=1, le=50),
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(Signal).where(Signal.market == market)
        .order_by(Signal.created_at.desc()).limit(limit)
    )).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/open")
async def open_signals(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: str = Query(...),
    types: str = Query("BUY,SELL"),
) -> list[dict[str, Any]]:
    type_list = [t.strip() for t in types.split(",")]
    rows = (await db.execute(
        select(Signal).where(
            Signal.market == market,
            Signal.signal_type.in_(type_list),
            Signal.closed_at.is_(None),
        ).order_by(Signal.created_at.desc())
    )).scalars().all()
    return [_to_dict(r) for r in rows]


@router.patch("/{signal_id}")
async def patch(
    signal_id: uuid.UUID,
    body: SignalUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = await db.get(Signal, signal_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


@router.post("/close-opposite")
async def close_opposite(
    body: CloseOppositeBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, int]:
    opp = "SELL" if body.new_signal_type == "BUY" else "BUY"
    result = await db.execute(
        update(Signal).where(
            Signal.market == body.market,
            Signal.signal_type == opp,
            Signal.closed_at.is_(None),
        ).values(closed_at=datetime.utcnow(), exit_price=body.exit_price)
    )
    await db.commit()
    return {"closed": result.rowcount}


@router.delete("/cleanup-hold")
async def cleanup_hold(
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(7, ge=1, le=30),
) -> dict[str, int]:
    """오래된 HOLD 시그널 삭제."""
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        delete(Signal).where(
            Signal.signal_type == "HOLD",
            Signal.created_at < cutoff,
        )
    )
    await db.commit()
    return {"deleted": result.rowcount}

"""TrackedAsset CRUD 라우터."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from models.tracked_asset import TrackedAsset
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.deps import get_db

router = APIRouter(prefix="/api/tracked-assets", tags=["tracked-assets"])


class TrackedAssetCreate(BaseModel):
    market: str
    intent: str = "watch"
    is_active: bool = True


class TrackedAssetUpdate(BaseModel):
    is_active: bool | None = None
    intent: str | None = None
    memo: str | None = None


@router.get("/active")
async def list_active(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(TrackedAsset).where(TrackedAsset.is_active.is_(True))
    )).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("")
async def get_by_market(
    db: Annotated[AsyncSession, Depends(get_db)],
    market: str = Query(...),
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(TrackedAsset).where(TrackedAsset.market == market)
    )).scalars().all()
    return [_to_dict(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: TrackedAssetCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = TrackedAsset(market=body.market, intent=body.intent, is_active=body.is_active)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


@router.patch("/{market}")
async def update(
    market: str,
    body: TrackedAssetUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = (await db.execute(
        select(TrackedAsset).where(TrackedAsset.market == market)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


class PriceItem(BaseModel):
    market: str
    price: float


@router.put("/prices")
async def update_prices(
    items: list[PriceItem],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, int]:
    """활성 종목 현재가 일괄 업데이��."""
    now = datetime.now(UTC)
    updated = 0
    for item in items:
        row = (await db.execute(
            select(TrackedAsset).where(
                TrackedAsset.market == item.market,
                TrackedAsset.is_active.is_(True),
            )
        )).scalar_one_or_none()
        if row:
            row.current_price = item.price
            row.price_updated_at = now
            updated += 1
    await db.commit()
    return {"updated": updated}


def _to_dict(r: TrackedAsset) -> dict[str, Any]:
    return {
        "id": str(r.id), "market": r.market, "intent": r.intent,
        "memo": r.memo, "is_active": r.is_active,
        "current_price": float(r.current_price) if r.current_price else None,
        "price_updated_at": r.price_updated_at.isoformat() if r.price_updated_at else None,
        "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat(),
    }

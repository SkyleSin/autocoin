"""TrackedAsset CRUD 라우터."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.tracked_asset import TrackedAsset
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


def _to_dict(r: TrackedAsset) -> dict[str, Any]:
    return {
        "id": str(r.id), "market": r.market, "intent": r.intent,
        "memo": r.memo, "is_active": r.is_active,
        "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat(),
    }

"""User 라우터 — 조회 + watchlist 관리."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from models.user import User
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.deps import get_db

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/active")
async def list_active(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(User).where(User.is_active.is_(True))
    )).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/by-telegram/{telegram_id}")
async def by_telegram(
    telegram_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = (await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return _to_dict(row)


@router.get("/{user_id}")
async def by_id(
    user_id: Annotated[int, Path(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = await db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return _to_dict(row)


@router.get("/{user_id}/full")
async def by_id_full(
    user_id: Annotated[int, Path(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """내부 서비스 전용 — 암호화된 API 키 포함."""
    row = await db.get(User, user_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not found",
        )
    return _to_dict_full(row)


class WatchlistUpdate(BaseModel):
    coin_watchlist: list[str]


@router.get("/{user_id}/watchlist")
async def get_watchlist(
    user_id: Annotated[int, Path(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[str]:
    row = await db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return row.coin_watchlist or []


@router.put("/{user_id}/watchlist")
async def update_watchlist(
    user_id: Annotated[int, Path(...)],
    body: WatchlistUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[str]:
    row = await db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    row.coin_watchlist = body.coin_watchlist
    await db.commit()
    await db.refresh(row)
    return row.coin_watchlist


def _to_dict(r: User) -> dict[str, Any]:
    """민감정보(API 키) 제외."""
    return {
        "id": r.id,
        "telegram_id": r.telegram_id,
        "username": r.username,
        "coin_watchlist": r.coin_watchlist,
        "risk_settings": r.risk_settings,
        "is_active": r.is_active,
        "created_at": r.created_at.isoformat(),
    }


def _to_dict_full(r: User) -> dict[str, Any]:
    """내부 서비스 전용 — 암호화된 API 키 포함."""
    d = _to_dict(r)
    d["bithumb_api_key"] = r.bithumb_api_key or ""
    d["bithumb_secret_key"] = r.bithumb_secret_key or ""
    return d

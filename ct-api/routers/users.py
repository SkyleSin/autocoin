"""User 조회 라우터 (읽기 전용, 민감정보 제외)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
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

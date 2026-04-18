"""유저 포트폴리오 CRUD 라우터."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user_portfolio import UserPortfolio
from utils.deps import get_db

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


class PortfolioItem(BaseModel):
    currency: str
    balance: Decimal
    locked: Decimal = Decimal("0")
    avg_buy_price: Decimal = Decimal("0")


class SyncRequest(BaseModel):
    """ct-worker가 빗썸 잔고 동기화 시 전송하는 페이로드."""
    user_id: int
    items: list[PortfolioItem]


def _to_dict(r: UserPortfolio) -> dict[str, Any]:
    return {
        "id": r.id,
        "user_id": r.user_id,
        "currency": r.currency,
        "balance": str(r.balance),
        "locked": str(r.locked),
        "avg_buy_price": str(r.avg_buy_price),
        "updated_at": r.updated_at.isoformat(),
    }


@router.get("/{user_id}")
async def get_user_portfolio(
    user_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """유저의 보유 자산 목록 조회."""
    rows = (await db.execute(
        select(UserPortfolio)
        .where(UserPortfolio.user_id == user_id)
        .order_by(UserPortfolio.currency)
    )).scalars().all()
    return [_to_dict(r) for r in rows]


@router.get("/{user_id}/{currency}")
async def get_user_currency(
    user_id: int,
    currency: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any] | None:
    """유저의 특정 코인 보유량 조회."""
    row = (await db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == user_id,
            UserPortfolio.currency == currency.upper(),
        )
    )).scalar_one_or_none()
    if row is None:
        return None
    return _to_dict(row)


@router.put("/sync", status_code=status.HTTP_200_OK)
async def sync_portfolio(
    req: SyncRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """빗썸 잔고와 동기화 — 전체 교체 (upsert).

    ct-worker가 빗썸 잔고를 조회한 뒤 이 엔드포인트로 전송.
    기존에 있지만 빗썸에 없는 코인은 삭제 (외부에서 출금된 경우).
    """
    user_id = req.user_id
    incoming_currencies = {item.currency.upper() for item in req.items}

    # 기존 포트폴리오 조회
    existing = (await db.execute(
        select(UserPortfolio).where(UserPortfolio.user_id == user_id)
    )).scalars().all()
    existing_map = {r.currency: r for r in existing}

    upserted = 0
    removed = 0

    # upsert
    for item in req.items:
        currency = item.currency.upper()
        if currency in existing_map:
            row = existing_map[currency]
            row.balance = item.balance
            row.locked = item.locked
            row.avg_buy_price = item.avg_buy_price
        else:
            db.add(UserPortfolio(
                user_id=user_id,
                currency=currency,
                balance=item.balance,
                locked=item.locked,
                avg_buy_price=item.avg_buy_price,
            ))
        upserted += 1

    # 빗썸에 없는 코인 삭제
    for currency, row in existing_map.items():
        if currency not in incoming_currencies:
            await db.delete(row)
            removed += 1

    await db.commit()
    return {"user_id": user_id, "upserted": upserted, "removed": removed}

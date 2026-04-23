"""유저 포트폴리오 CRUD 라우터."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, status
from models.trade import Trade
from models.user_portfolio import UserPortfolio
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.deps import get_db

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Shadow 가상 체결
# ---------------------------------------------------------------------------

FEE_RATE = Decimal("0.0025")  # 빗썸 0.25%


class ShadowExecuteRequest(BaseModel):
    """shadow 유저 가상 체결 요청."""
    user_id: int
    market: str
    side: str  # "buy" / "sell"
    price: float
    amount_krw: float = 0  # BUY 시 투입 금액
    sell_pct: int = 0  # SELL 시 매도 비율


@router.post("/shadow-execute", status_code=status.HTTP_200_OK)
async def shadow_execute(
    req: ShadowExecuteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """shadow 유저 가상 체결 — trade 기록 + 포트폴리오 갱신."""
    price = Decimal(str(req.price))
    currency = req.market.split("-")[1] if "-" in req.market else ""

    # 현재 포트폴리오 조회
    krw_row = (await db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == req.user_id,
            UserPortfolio.currency == "KRW",
        )
    )).scalar_one_or_none()
    coin_row = (await db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == req.user_id,
            UserPortfolio.currency == currency,
        )
    )).scalar_one_or_none()

    krw_balance = krw_row.balance if krw_row else Decimal("0")
    coin_balance = coin_row.balance if coin_row else Decimal("0")

    if req.side == "buy":
        amount = Decimal(str(req.amount_krw))
        if amount <= 0 or amount > krw_balance:
            return {"executed": False, "reason": "잔고 부족"}
        fee = amount * FEE_RATE
        qty = (amount - fee) / price

        # KRW 차감
        if krw_row:
            krw_row.balance -= amount
        # 코인 추가 (평균 매수가 갱신)
        if coin_row and coin_row.balance > 0:
            total_cost = coin_row.avg_buy_price * coin_row.balance + price * qty
            coin_row.balance += qty
            coin_row.avg_buy_price = total_cost / coin_row.balance
        elif coin_row:
            coin_row.balance = qty
            coin_row.avg_buy_price = price
        else:
            db.add(UserPortfolio(
                user_id=req.user_id, currency=currency,
                balance=qty, avg_buy_price=price,
            ))
        # trade 기록
        trade = Trade(
            user_id=req.user_id, market=req.market, side="buy",
            volume=qty, price_krw=price,
            status="shadow", notes="shadow auto-execute",
            executed_at=datetime.now(UTC),
        )
        db.add(trade)
        await db.commit()
        logger.info(
            "shadow BUY %s: %.0f원 → %.8f개 (fee=%.0f)",
            req.market, amount, qty, fee,
        )
        return {
            "executed": True, "side": "buy",
            "qty": float(qty), "amount_krw": float(amount),
            "fee": float(fee), "trade_id": str(trade.id),
        }

    elif req.side == "sell":
        if coin_balance <= 0:
            return {"executed": False, "reason": "보유량 없음"}
        sell_pct = min(req.sell_pct, 100)
        sell_qty = coin_balance * Decimal(str(sell_pct)) / Decimal("100")
        sell_amount = sell_qty * price
        fee = sell_amount * FEE_RATE
        net = sell_amount - fee

        # 코인 차감
        if coin_row:
            coin_row.balance -= sell_qty
            if coin_row.balance <= 0:
                coin_row.balance = Decimal("0")
        # KRW 추가
        if krw_row:
            krw_row.balance += net
        else:
            db.add(UserPortfolio(
                user_id=req.user_id, currency="KRW",
                balance=net,
            ))
        # trade 기록
        trade = Trade(
            user_id=req.user_id, market=req.market, side="sell",
            volume=sell_qty, price_krw=price,
            status="shadow", notes="shadow auto-execute",
            executed_at=datetime.now(UTC),
        )
        db.add(trade)
        await db.commit()
        logger.info(
            "shadow SELL %s: %.8f개 × %.0f = %.0f원 (fee=%.0f)",
            req.market, sell_qty, price, sell_amount, fee,
        )
        return {
            "executed": True, "side": "sell",
            "qty": float(sell_qty), "amount_krw": float(sell_amount),
            "fee": float(fee), "trade_id": str(trade.id),
        }

    return {"executed": False, "reason": "invalid side"}

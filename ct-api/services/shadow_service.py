"""섀도우 트레이딩 서비스 — 가상 매매 기록/요약."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.shadow_trade import ShadowPortfolio, ShadowTrade

logger = logging.getLogger(__name__)

FEE_RATE = Decimal("0.0025")
INITIAL_KRW = Decimal("1000000")
MAX_PER_TRADE_PCT = Decimal("15")
MIN_POSITION_KRW = Decimal("5000")

_SELL_RATIO = [(0.8, 80), (0.6, 60), (0.4, 40), (0.0, 20)]


def _sell_pct_from_confidence(confidence: float) -> int:
    for threshold, pct in _SELL_RATIO:
        if confidence >= threshold:
            return pct
    return 20


async def _get_krw(session: AsyncSession) -> Decimal:
    row = (await session.execute(
        select(ShadowPortfolio).where(ShadowPortfolio.market == "_KRW")
    )).scalar_one_or_none()
    if row is None:
        session.add(ShadowPortfolio(
            market="_KRW", qty=Decimal("0"), avg_price=Decimal("0"),
            krw_balance=INITIAL_KRW,
        ))
        await session.flush()
        return INITIAL_KRW
    return row.krw_balance


async def _set_krw(session: AsyncSession, amount: Decimal) -> None:
    row = (await session.execute(
        select(ShadowPortfolio).where(ShadowPortfolio.market == "_KRW")
    )).scalar_one_or_none()
    if row:
        row.krw_balance = amount
    else:
        session.add(ShadowPortfolio(
            market="_KRW", qty=Decimal("0"), avg_price=Decimal("0"),
            krw_balance=amount,
        ))


async def _get_position(session: AsyncSession, market: str) -> ShadowPortfolio | None:
    return (await session.execute(
        select(ShadowPortfolio).where(ShadowPortfolio.market == market)
    )).scalar_one_or_none()


async def record_shadow_trade(
    session: AsyncSession,
    *,
    market: str,
    signal_type: str,
    close_price: float,
    confidence: float,
    signal_id=None,
) -> ShadowTrade | None:
    price = Decimal(str(close_price))
    krw = await _get_krw(session)
    pos = await _get_position(session, market)

    if signal_type == "BUY":
        if pos and pos.qty > 0:
            return None
        conf_scale = max(Decimal("0.5"), Decimal(str(min(confidence, 1.0))))
        alloc_pct = MAX_PER_TRADE_PCT * conf_scale
        amount = krw * alloc_pct / Decimal("100")
        if amount < MIN_POSITION_KRW or amount > krw:
            return None
        fee = amount * FEE_RATE
        qty = (amount - fee) / price
        await _set_krw(session, krw - amount)
        if pos:
            pos.qty = qty
            pos.avg_price = price
        else:
            session.add(ShadowPortfolio(
                market=market, qty=qty, avg_price=price, krw_balance=Decimal("0"),
            ))
        trade = ShadowTrade(
            market=market, side="BUY", price=price, qty=qty,
            amount_krw=amount, fee=fee, sell_pct=0,
            confidence=confidence, signal_id=signal_id,
        )
        session.add(trade)
        await session.commit()
        return trade

    elif signal_type == "SELL":
        if not pos or pos.qty <= 0:
            return None
        sell_pct = _sell_pct_from_confidence(confidence)
        sell_qty = pos.qty * Decimal(str(sell_pct)) / Decimal("100")
        sell_amount = sell_qty * price
        fee = sell_amount * FEE_RATE
        net = sell_amount - fee
        pnl = net - sell_qty * pos.avg_price
        pos.qty -= sell_qty
        remaining_value = pos.qty * price
        new_krw = krw + net
        if remaining_value < MIN_POSITION_KRW and pos.qty > 0:
            leftover = pos.qty * price
            leftover_fee = leftover * FEE_RATE
            new_krw += leftover - leftover_fee
            pnl += leftover - leftover_fee - pos.qty * pos.avg_price
            sell_qty += pos.qty
            sell_amount += leftover
            fee += leftover_fee
            pos.qty = Decimal("0")
        await _set_krw(session, new_krw)
        trade = ShadowTrade(
            market=market, side="SELL", price=price, qty=sell_qty,
            amount_krw=sell_amount, fee=fee, sell_pct=sell_pct,
            confidence=confidence, pnl=pnl, signal_id=signal_id,
        )
        session.add(trade)
        await session.commit()
        return trade

    return None


async def get_shadow_summary(session: AsyncSession) -> dict:
    krw = await _get_krw(session)
    positions = (await session.execute(
        select(ShadowPortfolio).where(
            ShadowPortfolio.market != "_KRW", ShadowPortfolio.qty > 0,
        )
    )).scalars().all()
    trades = (await session.execute(
        select(ShadowTrade).where(ShadowTrade.side == "SELL")
    )).scalars().all()
    total_pnl = sum(float(t.pnl) for t in trades)
    total_fees = float((await session.execute(
        select(func.sum(ShadowTrade.fee))
    )).scalar() or 0)
    wins = sum(1 for t in trades if t.pnl > 0)
    buy_count = (await session.execute(
        select(func.count()).select_from(ShadowTrade).where(ShadowTrade.side == "BUY")
    )).scalar() or 0
    total_value = float(krw) + sum(float(p.qty * p.avg_price) for p in positions)
    return {
        "krw_balance": float(krw),
        "positions": [
            {"market": p.market, "qty": float(p.qty), "avg_price": float(p.avg_price)}
            for p in positions
        ],
        "total_pnl": total_pnl,
        "total_fees": total_fees,
        "buy_count": buy_count,
        "sell_count": len(trades),
        "win_rate": wins / len(trades) * 100 if trades else 0,
        "total_value": total_value,
        "return_pct": (total_value - float(INITIAL_KRW)) / float(INITIAL_KRW) * 100,
    }

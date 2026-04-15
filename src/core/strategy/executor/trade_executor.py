"""매매 실행기 — 시그널 → 잔고 확인 → 리스크 검증 → 주문."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models.trade import Trade
from utils.config import Settings

from .risk_manager import check_risk

logger = logging.getLogger(__name__)


async def execute_trade(
    session: AsyncSession,
    settings: Settings,
    *,
    market: str,
    side: str,
    close_price: float,
    signal_type: str,
    confidence: float,
    krw_balance: float,
    coin_balance: float = 0.0,
) -> dict[str, Any]:
    """매매 실행 흐름.

    1. 리스크 검증
    2. 주문 수량/금액 결정
    3. Trade 기록 생성 (실제 주문은 place_order Job으로)

    Returns:
        실행 결과 dict (trade_id, order_content 등)
    """
    # 매수/매도 방향 결정
    bithumb_side = "bid" if side == "BUY" else "ask"

    # 매매 금액 결정 (confidence 기반 스케일링)
    # 포지션 사이징은 calculate_position에서 처리, 여기서는 가용 잔고 기반
    scale = max(0.5, min(confidence, 1.0))
    available = krw_balance - settings.risk_min_krw_reserve
    intended_amount = max(0, available) * scale

    # 매도 시: 보유 코인 기준
    if bithumb_side == "ask":
        intended_amount = coin_balance * close_price
        if coin_balance <= 0:
            return {
                "status": "rejected",
                "reason": "매도할 코인이 없습니다.",
            }

    # 리스크 검증
    risk = await check_risk(
        session,
        settings,
        side=bithumb_side,
        market=market,
        krw_balance=krw_balance,
        intended_amount_krw=intended_amount,
    )

    if not risk.allowed:
        logger.warning("리스크 검증 실패: %s", risk.reason)
        return {
            "status": "rejected",
            "reason": risk.reason,
        }

    # 주문 수량 계산
    if bithumb_side == "bid":
        # 매수: KRW 금액 기준 → 시장가 매수
        order_content = {
            "market": market,
            "side": "bid",
            "price": str(int(risk.max_amount_krw)),
            "ord_type": "price",  # 시장가 매수 (금액 지정)
        }
    else:
        # 매도: 코인 수량 기준 → 시장가 매도
        sell_volume = coin_balance * scale
        order_content = {
            "market": market,
            "side": "ask",
            "volume": str(sell_volume),
            "ord_type": "market",  # 시장가 매도
        }

    # Trade 기록 생성
    trade = Trade(
        market=market,
        side="buy" if bithumb_side == "bid" else "sell",
        volume=Decimal(str(order_content.get("volume", "0"))),
        price_krw=Decimal(str(close_price)),
        status="pending",
        notes=f"signal={signal_type} conf={confidence:.2f}",
    )
    session.add(trade)
    await session.flush()

    return {
        "status": "approved",
        "trade_id": str(trade.id),
        "order_content": order_content,
        "risk_check": risk.reason,
        "intended_amount_krw": risk.max_amount_krw,
    }

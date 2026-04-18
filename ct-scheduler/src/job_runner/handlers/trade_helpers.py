"""매매 핸들러 공통 헬퍼 — 리스크 거부 처리, 주문 생성, 한도 체크."""
from __future__ import annotations

import logging
from typing import Any

from infra.redis_pubsub import publish_signal_alert
from utils.config import Settings

logger = logging.getLogger(__name__)


async def handle_risk_denied(
    settings: Settings,
    accounts: list[dict[str, Any]],
    content: dict[str, Any],
    bithumb_side: str,
    market: str,
    side: str,
    risk: Any,
    confidence: float,
    intended: float,
) -> None:
    """리스크 거부 시 알림 또는 리밸런싱 제안."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if bithumb_side == "bid" and "잔고 부족" in risk.reason and token and chat_id:
        from job_runner.handlers.trade_rebalance import suggest_rebalance
        await suggest_rebalance(
            settings, accounts,
            buy_market=market, buy_price=float(content.get("price", 0)),
            buy_confidence=confidence, intended_krw=intended,
        )
    elif token and chat_id:
        await publish_signal_alert(
            chat_id,
            f"❌ `{market}` {side} 주문 거부\n사유: {risk.reason}",
        )


def build_order_content(
    bithumb_side: str,
    market: str,
    content: dict[str, Any],
    risk: Any,
    coin_balance: float,
    pos: Any | None,
    settings: Settings,
    krw_balance: float,
    coin_currency: str,
) -> dict[str, Any]:
    """place_order에 전달할 주문 content 생성."""
    if bithumb_side == "bid":
        return {
            "market": market,
            "side": "bid",
            "price": str(int(risk.max_amount_krw)),
            "ord_type": "price",
        }

    sell_pct = int(content.get("sell_pct", 100))
    sell_volume = coin_balance * (sell_pct / 100)
    return {
        "market": market,
        "side": "ask",
        "volume": str(sell_volume),
        "ord_type": "market",
    }


def check_coin_limit(
    settings: Settings,
    accounts: list[dict[str, Any]],
    market: str,
    krw_balance: float,
    coin_balance: float,
    price: float,
) -> bool:
    """코인당 포지션 한도 체크. True면 허용."""
    if settings.risk_max_per_coin_pct <= 0:
        return True
    total_capital = krw_balance + sum(
        float(a.get("balance", 0)) * float(a.get("avg_buy_price", 0))
        for a in accounts
        if a.get("currency") not in ("KRW", "")
        and float(a.get("balance", 0)) > 0
    )
    coin_value = (
        coin_balance * price if coin_balance > 0 and price > 0 else 0
    )
    if total_capital > 0:
        pct = coin_value / total_capital * 100
        if pct >= settings.risk_max_per_coin_pct:
            logger.info("코인당 한도 초과: %s", market)
            return False
    return True

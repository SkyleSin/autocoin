"""잔고 확인 후 매매 체이닝 로직 — API 호출 기반."""

from __future__ import annotations

import logging
from typing import Any

from core.strategy.executor.position_sizing import calculate_position
from infra.api.signal import get_latest_signal
from infra.api.strategy import check_risk as api_check_risk
from infra.redis_pubsub import publish_signal_alert
from utils.config import Settings

logger = logging.getLogger(__name__)


async def chain_trade_from_balance(
    settings: Settings,
    accounts: list[dict[str, Any]],
    content: dict[str, Any],
) -> dict[str, Any] | None:
    """check_balance 후 리스크 검증 -> place_order 준비.

    Returns:
        주문 파라미터 dict. 거부/실패 시 None.
    """
    market = str(content.get("market", ""))
    side = str(content.get("side", ""))
    price = float(content.get("price", 0))

    if not market or side not in ("BUY", "SELL"):
        return None

    bithumb_side = "bid" if side == "BUY" else "ask"
    krw_acc = next((a for a in accounts if a.get("currency") == "KRW"), None)
    krw_balance = float(krw_acc["balance"]) if krw_acc else 0

    coin_currency = market.split("-")[1] if "-" in market else ""
    coin_acc = next(
        (a for a in accounts if a.get("currency") == coin_currency), None,
    )
    coin_balance = float(coin_acc["balance"]) if coin_acc else 0

    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if bithumb_side == "ask" and coin_balance <= 0:
        if token and chat_id:
            await publish_signal_alert(
                chat_id, f"❌ `{market}` 매도 불가 — 보유 코인 없음",
            )
        return None

    last_signal = await get_latest_signal(market)
    indicators = last_signal.get("indicators") if last_signal else None
    confidence = float(last_signal.get("confidence", 0.5)) if last_signal else 0.5
    patterns = None
    if indicators and isinstance(indicators.get("patterns"), dict):
        patterns = indicators["patterns"].get("patterns", [])

    pos = None
    if bithumb_side == "bid":
        from job_runner.handlers.trade_helpers import check_coin_limit
        if not check_coin_limit(
            settings, accounts, market, krw_balance, coin_balance, price,
        ):
            return None
        pos = calculate_position(
            krw_balance, patterns=patterns, confidence=confidence,
            indicators=indicators,
            max_per_trade_pct=settings.risk_max_per_trade_pct,
        )
        intended = pos.amount_krw
    else:
        intended = coin_balance * price

    risk_resp = await api_check_risk({
        "side": bithumb_side,
        "market": market,
        "krw_balance": krw_balance,
        "intended_amount_krw": intended,
    })
    risk = _RiskResult(
        allowed=risk_resp["allowed"],
        reason=risk_resp["reason"],
        max_amount_krw=risk_resp["max_amount_krw"],
    )
    if not risk.allowed:
        from job_runner.handlers.trade_helpers import handle_risk_denied
        await handle_risk_denied(
            settings, accounts, content,
            bithumb_side, market, side, risk, confidence, intended,
        )
        return None

    from job_runner.handlers.trade_helpers import build_order_content
    return build_order_content(
        bithumb_side, market, content, risk, coin_balance, pos,
        settings, krw_balance, coin_currency,
    )


class _RiskResult:
    """API 응답을 RiskCheck 호환 객체로 래핑."""

    __slots__ = ("allowed", "reason", "max_amount_krw")

    def __init__(self, allowed: bool, reason: str, max_amount_krw: float) -> None:
        self.allowed = allowed
        self.reason = reason
        self.max_amount_krw = max_amount_krw

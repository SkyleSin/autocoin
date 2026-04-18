"""시그널 알림 발송 -- strategy_scheduler에서 BUY/SELL 시 호출.

LLM 없이 기술분석 시그널을 직접 텔레그램으로 발송한다.
"""

from __future__ import annotations

import asyncio
import logging

from infra.api.active_alert import check_alert, delete_alerts
from infra.api.shadow import record_shadow_trade
from utils.config import Settings

from core.strategy.signal_alert_sender import _send_alert

logger = logging.getLogger(__name__)


async def notify_signal(
    settings: Settings,
    *,
    market: str,
    timeframe: str,
    signal_type: str,
    confidence: float,
    reasons: list[str],
    indicators: dict | None,
    close_price: float,
    user_data: dict | None = None,
) -> None:
    """BUY/SELL 시그널을 텔레그램으로 알림 + 섀도우 기록."""
    chat_id = str(user_data["telegram_id"]) if user_data else settings.telegram_chat_id
    if not (settings.telegram_bot_token and chat_id):
        return

    force_full_sell = False
    if signal_type == "SELL":
        has_balance, force_full_sell = await _check_sell_balance(
            market, timeframe, close_price, settings,
        )
        if not has_balance:
            return

    alert_resp = await check_alert({
        "market": market, "timeframe": timeframe,
        "signal_type": signal_type, "confidence": confidence,
        "reasons": reasons, "indicators": indicators,
        "remind_interval_sec": settings.alert_remind_interval_sec,
    })
    action = alert_resp.get("action", "skip")

    if action not in ("new", "changed", "remind"):
        return

    await _send_alert(
        settings,
        market=market, timeframe=timeframe,
        signal_type=signal_type, confidence=confidence,
        reasons=reasons, indicators=indicators,
        close_price=close_price,
        action=action, force_full_sell=force_full_sell,
        chat_id=chat_id,
    )

    try:
        await record_shadow_trade({
            "market": market, "signal_type": signal_type,
            "close_price": close_price, "confidence": confidence,
        })
    except Exception:
        logger.warning("shadow trade 기록 실패", exc_info=True)


async def _check_sell_balance(
    market: str, timeframe: str, close_price: float, settings: Settings,
) -> tuple[bool, bool]:
    """SELL 보유량 확인. bithumb 직접 호출."""
    try:
        from bithumb.account import BithumbAccount

        client = BithumbAccount(settings.bithumb_api_key, settings.bithumb_secret_key)
        accounts = await asyncio.to_thread(client.get_balances)
        coin = market.split("-")[1] if "-" in market else ""
        acc = next((a for a in accounts if a.get("currency") == coin), None)
        balance = float(acc["balance"]) if acc else 0

        if balance <= 0:
            await delete_alerts(market, timeframe)
            logger.info("SELL 보유량 없음 -> ActiveAlert 삭제: %s", market)
            return False, False

        krw_value = balance * close_price
        if krw_value <= 5000:
            logger.info(
                "소액 보유 전량 매도 전환: %s (%.0f원)", market, krw_value,
            )
            return True, True

        return True, False
    except Exception:
        logger.warning("SELL 보유 확인 실패, 알림 생략", exc_info=True)
        return False, False

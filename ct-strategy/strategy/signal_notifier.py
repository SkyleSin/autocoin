"""시그널 알림 발송 — BUY/SELL 시 알림 판단 후 ct-telegram에 발행."""

from __future__ import annotations

import logging

from infra.api.active_alert import create_alert, find_alert, update_alert
from infra.redis_pubsub import publish_signal_alert
from utils.alert_logic import AlertAction, decide_alert_action
from utils.config import Settings

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
    """BUY/SELL 시그널 알림 판단 + 발행."""
    chat_id = str(user_data["telegram_id"]) if user_data else settings.telegram_chat_id
    if not (settings.telegram_bot_token and chat_id):
        return

    user_id = user_data["id"] if user_data else None

    # 1. 기존 알림 조회
    existing = await find_alert(market, timeframe, user_id=user_id)

    # 2. 판단 (순수 함수)
    action, updates = decide_alert_action(
        existing, signal_type, confidence, reasons, indicators,
        remind_interval_sec=settings.alert_remind_interval_sec,
    )

    # 3. DB 반영
    if updates:
        if updates.pop("_create", False):
            await create_alert({
                "user_id": user_id,
                "market": market,
                "timeframe": timeframe,
                **updates,
            })
        else:
            await update_alert(market, timeframe, updates, user_id=user_id)

    if action not in (AlertAction.NEW, AlertAction.CHANGED, AlertAction.REMIND):
        return

    # 4. 시그널 알림 발행
    await publish_signal_alert(
        chat_id=chat_id,
        market=market,
        signal_type=signal_type,
        close_price=close_price,
        confidence=confidence,
        reasons=reasons,
        indicators=indicators,
        action=action,
        force_full_sell=False,
    )
    logger.info(
        "signal_alert 발행: %s %s %s (conf=%.2f action=%s)",
        market, timeframe, signal_type, confidence, action,
    )

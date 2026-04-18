"""시그널 알림 발송 — BUY/SELL 시 check_alert 후 ct-telegram에 발행."""

from __future__ import annotations

import logging

from infra.api.active_alert import check_alert
from infra.redis_pubsub import publish_signal_alert
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
    """BUY/SELL 시그널을 Redis pub/sub으로 ct-telegram에 전달.

    잔고 확인은 하지 않음 — 주문 실행 시 ct-worker가 담당.
    """
    chat_id = str(user_data["telegram_id"]) if user_data else settings.telegram_chat_id
    if not (settings.telegram_bot_token and chat_id):
        return

    alert_resp = await check_alert({
        "market": market,
        "timeframe": timeframe,
        "signal_type": signal_type,
        "confidence": confidence,
        "reasons": reasons,
        "indicators": indicators,
        "remind_interval_sec": settings.alert_remind_interval_sec,
    })
    action = alert_resp.get("action", "skip")

    if action not in ("new", "changed", "remind"):
        return

    await publish_signal_alert(
        chat_id=chat_id,
        market=market,
        signal_type=signal_type,
        close_price=close_price,
        confidence=confidence,
        reasons=reasons,
        indicators=indicators,
        action=action,
        force_full_sell=False,  # 잔고 확인 없음
    )
    logger.info(
        "signal_alert 발행: %s %s %s (conf=%.2f action=%s)",
        market, timeframe, signal_type, confidence, action,
    )

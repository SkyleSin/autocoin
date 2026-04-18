"""시그널 알림 — 원시 데이터를 Redis에 발행 (포맷은 ct-telegram 담당)."""

from __future__ import annotations

import logging

from infra.api.candle import get_candles
from infra.redis_pubsub import publish_signal_alert
from utils.config import Settings

from core.strategy.chart import generate_chart_from_candle_dicts
from core.strategy.types import AlertAction

logger = logging.getLogger(__name__)


async def _send_alert(
    settings: Settings,
    *,
    market: str,
    timeframe: str,
    signal_type: str,
    confidence: float,
    reasons: list[str],
    indicators: dict | None,
    close_price: float,
    action: str,
    force_full_sell: bool,
    chat_id: str = "",
) -> None:
    """차트 생성 + 원시 데이터를 Redis signal_alert로 발행."""
    chart_image = await _generate_chart(
        market, timeframe, signal_type, indicators,
    )

    target_chat_id = chat_id or settings.telegram_chat_id
    await publish_signal_alert(
        chat_id=target_chat_id,
        market=market,
        signal_type=signal_type,
        close_price=close_price,
        confidence=confidence,
        reasons=reasons,
        indicators=indicators,
        action=action,
        force_full_sell=force_full_sell,
        chart_bytes=chart_image,
    )


def _action_prefix(action: str) -> str:
    if action == AlertAction.CHANGED:
        return "change"
    if action == AlertAction.REMIND:
        return "remind"
    return "new"


async def _generate_chart(
    market: str, timeframe: str,
    signal_type: str, indicators: dict | None,
) -> bytes | None:
    """차트 이미지 생성 -- API에서 캔들 조회."""
    try:
        rows = await get_candles(market, timeframe, limit=200, order="asc")
        if len(rows) >= 30:
            return generate_chart_from_candle_dicts(
                rows,
                market=market,
                signal_type=signal_type,
                sr_analysis=indicators.get("support_resistance")
                if indicators
                else None,
                pattern_analysis=indicators.get("patterns")
                if indicators
                else None,
            )
    except Exception:
        logger.warning("차트 이미지 생성 실패", exc_info=True)
    return None

"""시그널 알림 메시지 생성 + 텔레그램 발송."""

from __future__ import annotations

import logging

from core.analysis.chart import generate_chart_from_candle_dicts
from infra.api.candle import get_candles
from infra.redis_pubsub import publish_signal_alert
from services.alert_service import AlertAction
from telegram.bot import make_signal_buttons
from telegram.messages import format_signal_alert
from utils.config import Settings

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
    """알림 메시지 생성 + 차트 + 텔레그램 발송."""
    prefix = _action_prefix(action)

    alert_text = prefix + format_signal_alert(
        market=market,
        close_price=close_price,
        signal_type=signal_type,
        confidence=confidence,
        reasons=reasons,
        indicators=indicators,
    )

    chart_image = await _generate_chart(
        market, timeframe, signal_type, indicators,
    )

    sell_rec_pct = 0
    sell_choices = None
    if signal_type == "SELL":
        sell_rec_pct, sell_choices = _calc_sell_recommendation(
            indicators, confidence, force_full_sell,
        )
        alert_text += f"\n\n📊 *매도 추천:* `{sell_rec_pct}%`"
        if sell_rec_pct < 100:
            alert_text += " (부분 매도 -> 재진입 가능)"

    buttons = None
    if signal_type in ("BUY", "SELL") and market:
        buttons = make_signal_buttons(
            market, signal_type, close_price,
            sell_recommended_pct=sell_rec_pct,
            sell_choices=sell_choices,
        )

    target_chat_id = chat_id or settings.telegram_chat_id
    await publish_signal_alert(
        target_chat_id, alert_text,
        photo_bytes=chart_image, reply_markup=buttons,
    )


def _action_prefix(action: str) -> str:
    if action == AlertAction.CHANGED:
        return "🔄 *시그널 변경!*\n\n"
    if action == AlertAction.REMIND:
        return "🔔 *재알림*\n\n"
    return ""


def _calc_sell_recommendation(
    indicators: dict | None,
    confidence: float,
    force_full_sell: bool,
) -> tuple[int, list[int] | None]:
    """매도 추천 비율 계산."""
    from core.strategy.executor.position_sizing import (
        calculate_sell_recommendation,
    )

    pat_list = None
    if indicators and isinstance(indicators.get("patterns"), dict):
        pat_list = indicators["patterns"].get("patterns", [])
    rec = calculate_sell_recommendation(pat_list, confidence=confidence)
    pct = rec.recommended_pct
    choices = rec.choices
    if force_full_sell and pct < 100:
        pct = 100
        choices = None
    return pct, choices


async def _generate_chart(
    market: str, timeframe: str,
    signal_type: str, indicators: dict | None,
) -> bytes | None:
    """차트 이미지 생성 — API에서 캔들 조회."""
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

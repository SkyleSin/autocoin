"""Redis send_message 채널 구독 + 텔레그램 API 전송."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from infra.redis_client import get_redis
from utils.config import Settings

logger = logging.getLogger("ct-telegram.subscriber")


async def send_message_subscriber(
    settings: Settings, shutdown: asyncio.Event,
) -> None:
    """Redis sub 루프 -- send_message 채널 구독 후 텔레그램 API 전송."""
    from telegram.bot import send_message, send_photo

    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("send_message")
    logger.info("send_message 채널 구독 시작")

    try:
        while not shutdown.is_set():
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0,
            )
            if msg is None:
                await asyncio.sleep(0.1)
                continue
            try:
                payload: dict[str, Any] = json.loads(msg["data"])
            except (json.JSONDecodeError, TypeError, KeyError):
                logger.warning("잘못된 메시지 형식: %s", msg)
                continue
            await _dispatch(settings, payload, send_message, send_photo)
    finally:
        await pubsub.unsubscribe("send_message")
        await pubsub.aclose()
        logger.info("send_message 구독 종료")


async def _dispatch(
    settings: Settings,
    payload: dict[str, Any],
    send_message_fn: Any,
    send_photo_fn: Any,
) -> None:
    """메시지 타입별 분기 처리."""
    msg_type = payload.get("type", "text")
    chat_id = payload.get("chat_id", "")
    token = settings.telegram_bot_token

    if msg_type == "signal_alert":
        await _handle_signal_alert(
            token, chat_id, payload, send_message_fn, send_photo_fn,
        )
    elif msg_type == "photo":
        text = payload.get("text") or payload.get("caption", "")
        reply_markup = payload.get("reply_markup")
        photo_b64 = payload.get("photo_b64")
        photo_bytes = base64.b64decode(photo_b64) if photo_b64 else None
        if photo_bytes:
            await send_photo_fn(
                token, chat_id, photo_bytes,
                caption=text, reply_markup=reply_markup,
            )
        else:
            await send_message_fn(
                token, chat_id, text, reply_markup=reply_markup,
            )
    else:
        text = payload.get("text") or payload.get("caption", "")
        reply_markup = payload.get("reply_markup")
        await send_message_fn(
            token, chat_id, text, reply_markup=reply_markup,
        )


async def _handle_signal_alert(
    token: str,
    chat_id: str,
    data: dict[str, Any],
    send_message_fn: Any,
    send_photo_fn: Any,
) -> None:
    """signal_alert 원시 데이터를 포맷 + 발송."""
    from telegram.bot import make_signal_buttons
    from telegram.messages import format_signal_alert

    market = data.get("market", "")
    signal_type = data.get("signal_type", "")
    close_price = data.get("close_price", 0)
    confidence = data.get("confidence", 0)
    reasons = data.get("reasons", [])
    indicators = data.get("indicators")
    action = data.get("action", "new")
    force_full_sell = data.get("force_full_sell", False)
    chart_b64 = data.get("chart_b64")

    prefix = _action_prefix(action)
    alert_text = prefix + format_signal_alert(
        market=market, close_price=close_price,
        signal_type=signal_type, confidence=confidence,
        reasons=reasons, indicators=indicators,
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

    chart_bytes = base64.b64decode(chart_b64) if chart_b64 else None
    if chart_bytes:
        await send_photo_fn(
            token, chat_id, chart_bytes,
            caption=alert_text, reply_markup=buttons,
        )
    else:
        await send_message_fn(
            token, chat_id, alert_text, reply_markup=buttons,
        )


def _action_prefix(action: str) -> str:
    if action in ("changed", "CHANGED"):
        return "🔄 *시그널 변경!*\n\n"
    if action in ("remind", "REMIND"):
        return "🔔 *재알림*\n\n"
    return ""


def _calc_sell_recommendation(
    indicators: dict | None, confidence: float, force_full_sell: bool,
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

"""Redis send_message 채널 구독 → 텔레그램 API 전송."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from infra.redis_client import get_redis
from telegram.bot import make_signal_buttons, send_message, send_photo
from telegram.messages import format_signal_alert
from utils.config import Settings

logger = logging.getLogger("ct-telegram.subscriber")


async def send_message_subscriber(settings: Settings) -> None:
    """Redis Pub/Sub 루프 — send_message 채널 구독 후 텔레그램 발송."""
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("send_message")
    logger.info("send_message 채널 구독 시작")

    try:
        while True:
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

            try:
                await _dispatch(settings, payload)
            except Exception:
                logger.exception("메시지 처리 실패: %s", payload.get("type"))

    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe("send_message")
        await pubsub.aclose()
        logger.info("send_message 구독 종료")


async def _dispatch(settings: Settings, payload: dict[str, Any]) -> None:
    """메시지 타입별 분기."""
    token = settings.telegram_bot_token
    msg_type = payload.get("type", "text")
    chat_id = payload.get("chat_id", "")

    if msg_type == "signal_alert":
        await _handle_signal_alert(token, chat_id, payload)

    elif msg_type == "photo":
        text = payload.get("text") or payload.get("caption", "")
        reply_markup = payload.get("reply_markup")
        photo_b64 = payload.get("photo_b64")
        photo_bytes = base64.b64decode(photo_b64) if photo_b64 else None
        if photo_bytes:
            await send_photo(token, chat_id, photo_bytes,
                             caption=text, reply_markup=reply_markup)
        else:
            await send_message(token, chat_id, text, reply_markup=reply_markup)

    else:
        text = payload.get("text") or payload.get("caption", "")
        reply_markup = payload.get("reply_markup")
        await send_message(token, chat_id, text, reply_markup=reply_markup)


async def _handle_signal_alert(
    token: str, chat_id: str, data: dict[str, Any],
) -> None:
    """signal_alert 원시 데이터를 포맷 + 발송."""
    market = data.get("market", "")
    signal_type = data.get("signal_type", "")
    close_price = data.get("close_price", 0)
    confidence = data.get("confidence", 0)
    reasons = data.get("reasons", [])
    indicators = data.get("indicators")
    action = data.get("action", "new")
    chart_b64 = data.get("chart_b64")

    prefix = _action_prefix(action)
    alert_text = prefix + format_signal_alert(
        market=market, close_price=close_price,
        signal_type=signal_type, confidence=confidence,
        reasons=reasons, indicators=indicators,
    )

    # ct-advisor가 추가한 포지셔닝 정보 반영
    sell_rec_pct = data.get("sell_recommended_pct", 0)
    sell_choices = data.get("sell_choices")
    buy_alloc_pct = data.get("buy_allocation_pct")

    buy_amount_krw = data.get("buy_amount_krw")
    krw_balance = data.get("krw_balance")
    coin_balance = data.get("coin_balance")

    if signal_type == "BUY" and buy_alloc_pct:
        amount_text = f"\n\n📊 *매수 추천:* 잔고의 `{buy_alloc_pct}%`"
        if buy_amount_krw:
            amount_text += f" (`{buy_amount_krw:,.0f}원`)"
        if krw_balance:
            amount_text += f"\n💰 KRW 잔고: `{krw_balance:,.0f}원`"
        alert_text += amount_text

    if signal_type == "SELL" and sell_rec_pct:
        sell_text = f"\n\n📊 *매도 추천:* `{sell_rec_pct}%`"
        if sell_rec_pct < 100:
            sell_text += " (부분 매도 -> 재진입 가능)"
        if coin_balance:
            sell_text += f"\n💰 보유량: `{coin_balance:.8f}`"
        alert_text += sell_text

    buttons = None
    if signal_type in ("BUY", "SELL") and market:
        buttons = make_signal_buttons(
            market, signal_type, close_price,
            sell_recommended_pct=sell_rec_pct,
            sell_choices=sell_choices,
        )

    chart_bytes = base64.b64decode(chart_b64) if chart_b64 else None
    if chart_bytes:
        await send_photo(token, chat_id, chart_bytes,
                         caption=alert_text, reply_markup=buttons)
    else:
        await send_message(token, chat_id, alert_text, reply_markup=buttons)


def _action_prefix(action: str) -> str:
    if action in ("changed", "CHANGED"):
        return "🔄 *시그널 변경!*\n\n"
    if action in ("remind", "REMIND"):
        return "🔔 *재알림*\n\n"
    return ""

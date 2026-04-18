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
    """Redis sub 루프 — send_message 채널 구독 후 텔레그램 API 전송."""
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
    text = payload.get("text") or payload.get("caption", "")
    reply_markup = payload.get("reply_markup")

    if msg_type in ("photo", "signal_alert"):
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
        await send_message_fn(
            token, chat_id, text, reply_markup=reply_markup,
        )

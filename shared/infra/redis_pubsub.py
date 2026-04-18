"""Redis Pub/Sub 유틸리티 — 텔레그램 알림 발행."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from infra.redis_client import get_redis

logger = logging.getLogger(__name__)

CHANNEL = "send_message"


async def publish_telegram(
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "Markdown",
    reply_markup: dict[str, Any] | None = None,
) -> None:
    """텍스트 메시지를 Redis pub/sub 채널로 발행."""
    if not chat_id:
        return
    msg: dict[str, Any] = {
        "type": "text",
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        msg["reply_markup"] = reply_markup
    r = get_redis()
    await r.publish(CHANNEL, json.dumps(msg, ensure_ascii=False))


async def publish_photo(
    chat_id: str,
    photo_bytes: bytes,
    caption: str = "",
    *,
    parse_mode: str = "Markdown",
    reply_markup: dict[str, Any] | None = None,
) -> None:
    """이미지 메시지를 Redis pub/sub 채널로 발행."""
    if not chat_id:
        return
    msg: dict[str, Any] = {
        "type": "photo",
        "chat_id": chat_id,
        "photo_b64": base64.b64encode(photo_bytes).decode(),
        "caption": caption,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        msg["reply_markup"] = reply_markup
    r = get_redis()
    await r.publish(CHANNEL, json.dumps(msg, ensure_ascii=False))


async def publish_signal_alert(
    chat_id: str,
    text: str,
    *,
    photo_bytes: bytes | None = None,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str = "Markdown",
) -> None:
    """복합 시그널 알림(텍스트+이미지+버튼)을 Redis 채널로 발행."""
    if not chat_id:
        return
    msg: dict[str, Any] = {
        "type": "signal_alert",
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if photo_bytes:
        msg["photo_b64"] = base64.b64encode(photo_bytes).decode()
    if reply_markup:
        msg["reply_markup"] = reply_markup
    r = get_redis()
    await r.publish(CHANNEL, json.dumps(msg, ensure_ascii=False))

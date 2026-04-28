"""Redis 메시지 발행 — Streams 기반 (Pub/Sub → XADD 전환).

함수 시그니처는 기존과 동일하게 유지하여 호출부 변경 최소화.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from infra.redis_stream import (
    STREAM_ADVISOR,
    STREAM_MASTER,
    STREAM_TELEGRAM,
    stream_publish,
)

logger = logging.getLogger(__name__)


async def publish_telegram(
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "Markdown",
    reply_markup: dict[str, Any] | None = None,
) -> None:
    """텍스트 메시지를 send_message 스트림으로 발행."""
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
    await stream_publish(STREAM_TELEGRAM, msg)


async def publish_photo(
    chat_id: str,
    photo_bytes: bytes,
    caption: str = "",
    *,
    parse_mode: str = "Markdown",
    reply_markup: dict[str, Any] | None = None,
) -> None:
    """이미지 메시지를 send_message 스트림으로 발행."""
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
    await stream_publish(STREAM_TELEGRAM, msg)


async def publish_signal_alert(
    chat_id: str,
    market: str,
    signal_type: str,
    close_price: float,
    confidence: float,
    reasons: list[str],
    indicators: dict | None,
    action: str,
    force_full_sell: bool,
    chart_bytes: bytes | None = None,
) -> None:
    """시그널 → advisor_request 스트림으로 발행."""
    if not chat_id:
        return
    msg: dict[str, Any] = {
        "type": "signal_alert",
        "chat_id": chat_id,
        "market": market,
        "signal_type": signal_type,
        "close_price": close_price,
        "confidence": confidence,
        "reasons": reasons,
        "indicators": indicators,
        "action": action,
        "force_full_sell": force_full_sell,
        "chart_b64": base64.b64encode(chart_bytes).decode() if chart_bytes else None,
    }
    await stream_publish(STREAM_ADVISOR, msg)


async def publish_defense_request(data: dict[str, Any]) -> None:
    """방어 시그널 → advisor_request 스트림으로 발행."""
    await stream_publish(STREAM_ADVISOR, data)


async def publish_to_telegram(msg: dict[str, Any]) -> None:
    """ct-advisor 처리 완료 메시지 → send_message 스트림으로 전달."""
    await stream_publish(STREAM_TELEGRAM, msg)


async def publish_master(
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "Markdown",
) -> None:
    """master 봇 스트림으로 시스템 메시지 발행."""
    if not chat_id:
        return
    msg: dict[str, Any] = {
        "type": "text",
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    await stream_publish(STREAM_MASTER, msg)

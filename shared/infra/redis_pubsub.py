"""Redis Pub/Sub 유틸리티 — 텔레그램 알림 발행."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from infra.redis_client import get_redis

logger = logging.getLogger(__name__)

CHANNEL = "send_message"
MASTER_CHANNEL = "send_master"
EVENT_CHANNEL = "worker_events"


ANALYSIS_CHANNEL = "analysis_ready"
ADVISOR_CHANNEL = "advisor_request"


async def publish_event(event: dict[str, Any]) -> None:
    """ct-worker 이벤트를 Redis 채널로 발행 (다른 컨테이너가 구독)."""
    r = get_redis()
    await r.publish(EVENT_CHANNEL, json.dumps(event, ensure_ascii=False))


async def publish_analysis_ready(market: str, timeframe: str) -> None:
    """지표 스냅샷 저장 완료 → ct-strategy에 전략 평가 요청."""
    r = get_redis()
    await r.publish(
        ANALYSIS_CHANNEL,
        json.dumps({"market": market, "timeframe": timeframe}, ensure_ascii=False),
    )


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
    """시그널 → ct-advisor 채널로 발행 (리스크/포지셔닝 처리 후 ct-telegram 전달)."""
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
    r = get_redis()
    await r.publish(ADVISOR_CHANNEL, json.dumps(msg, ensure_ascii=False))


async def publish_to_telegram(msg: dict[str, Any]) -> None:
    """ct-advisor가 처리 완료된 메시지를 ct-telegram(send_message)으로 전달."""
    r = get_redis()
    await r.publish(CHANNEL, json.dumps(msg, ensure_ascii=False))


async def publish_master(
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "Markdown",
) -> None:
    """master 봇 채널로 시스템 메시지 발행."""
    if not chat_id:
        return
    msg: dict[str, Any] = {
        "type": "text",
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    r = get_redis()
    await r.publish(MASTER_CHANNEL, json.dumps(msg, ensure_ascii=False))

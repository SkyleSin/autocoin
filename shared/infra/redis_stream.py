"""Redis Streams 유틸리티 — Consumer Group 기반 메시지 처리.

Pub/Sub 대체: 메시지 유실 방지 (ACK + XAUTOCLAIM 재시도).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from infra.redis_client import get_redis

logger = logging.getLogger(__name__)

# Stream 이름
STREAM_TELEGRAM = "stream:send_message"
STREAM_MASTER = "stream:send_master"
STREAM_ADVISOR = "stream:advisor_request"
STREAM_CANDLES = "stream:candles_collected"

# 스트림 최대 길이 (approximate trim)
_MAXLEN = 1000


async def stream_publish(stream: str, data: dict[str, Any]) -> str:
    """스트림에 메시지 발행 (XADD). 메시지 ID 반환."""
    r = get_redis()
    msg_id = await r.xadd(
        stream,
        {"data": json.dumps(data, ensure_ascii=False)},
        maxlen=_MAXLEN,
        approximate=True,
    )
    return msg_id


async def ensure_group(stream: str, group: str) -> None:
    """Consumer Group 생성 (이미 있으면 무시)."""
    r = get_redis()
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
        logger.info("stream group 생성: %s / %s", stream, group)
    except Exception as e:
        if "BUSYGROUP" in str(e):
            pass
        else:
            raise


async def stream_consume(
    stream: str,
    group: str,
    consumer: str,
    handler: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    max_retries: int = 3,
    claim_idle_ms: int = 60_000,
    block_ms: int = 2000,
    batch_size: int = 10,
) -> None:
    """스트림 소비 루프 — XREADGROUP + XAUTOCLAIM 재시도.

    - 신규 메시지: XREADGROUP ... > 로 소비
    - 실패 메시지: XAUTOCLAIM으로 재시도 (max_retries 초과 시 ACK + 로그)
    """
    r = get_redis()
    await ensure_group(stream, group)
    logger.info("stream_consume 시작: %s (group=%s, consumer=%s)", stream, group, consumer)

    try:
        while True:
            # 1) 미확인 메시지 재시도
            await _claim_and_retry(
                r, stream, group, consumer, handler, max_retries, claim_idle_ms,
            )

            # 2) 신규 메시지 소비
            try:
                results = await r.xreadgroup(
                    group, consumer, {stream: ">"}, count=batch_size, block=block_ms,
                )
                if not results:
                    continue
                for _, messages in results:
                    for msg_id, fields in messages:
                        await _process(r, stream, group, msg_id, fields, handler)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("xreadgroup 오류: %s", stream)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass

    logger.info("stream_consume 종료: %s", stream)


async def _claim_and_retry(
    r,  # noqa: ANN001
    stream: str,
    group: str,
    consumer: str,
    handler: Callable[[dict[str, Any]], Awaitable[None]],
    max_retries: int,
    claim_idle_ms: int,
) -> None:
    """미확인 메시지 XAUTOCLAIM → 재처리 or dead-letter ACK."""
    try:
        result = await r.xautoclaim(
            stream, group, consumer, min_idle_time=claim_idle_ms,
            start_id="0-0", count=10,
        )
        claimed = result[1] if len(result) > 1 else []
        for msg_id, fields in claimed:
            if not fields:
                await r.xack(stream, group, msg_id)
                continue

            pending = await r.xpending_range(
                stream, group, min=msg_id, max=msg_id, count=1,
            )
            times = pending[0]["times_delivered"] if pending else 0

            if times > max_retries:
                logger.warning(
                    "dead letter (재시도 %d회 초과): stream=%s msg_id=%s",
                    max_retries, stream, msg_id,
                )
                await r.xack(stream, group, msg_id)
                continue

            logger.info(
                "재시도 (%d/%d): stream=%s msg_id=%s",
                times, max_retries, stream, msg_id,
            )
            await _process(r, stream, group, msg_id, fields, handler)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("xautoclaim 오류: %s", stream)


async def _process(
    r,  # noqa: ANN001
    stream: str,
    group: str,
    msg_id: str,
    fields: dict[str, str],
    handler: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """메시지 처리 + ACK. 실패 시 ACK 안 함 → PEL에 남아 XAUTOCLAIM 대상."""
    try:
        payload = json.loads(fields.get("data", "{}"))
        await handler(payload)
        await r.xack(stream, group, msg_id)
    except Exception:
        logger.exception("메시지 처리 실패 (PEL 유지): stream=%s msg_id=%s", stream, msg_id)

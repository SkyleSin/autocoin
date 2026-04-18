"""Redis 연결 관리 — 싱글턴 연결 풀."""

from __future__ import annotations

import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_redis: Redis | None = None


async def init_redis(url: str) -> Redis:
    """Redis 연결 풀 초기화. lifespan에서 1회 호출."""
    global _redis
    _redis = Redis.from_url(url, decode_responses=True)
    await _redis.ping()
    logger.info("Redis 연결 완료: %s", url.split("@")[-1])
    return _redis


def get_redis() -> Redis:
    """현재 Redis 클라이언트 반환. init_redis 호출 전이면 RuntimeError."""
    if _redis is None:
        msg = "Redis가 초기화되지 않았습니다. init_redis()를 먼저 호출하세요."
        raise RuntimeError(msg)
    return _redis


async def close_redis() -> None:
    """Redis 연결 종료."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis 연결 종료")

"""Redis 분산 락 — 유저별 매매 동시 실행 방지."""

from __future__ import annotations

import logging
import uuid

from infra.redis_client import get_redis

logger = logging.getLogger(__name__)

# Lua: 소유자 확인 후 DEL (안전한 해제)
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


async def acquire_trade_lock(
    user_id: int | str,
    ttl: int = 30,
) -> str | None:
    """유저별 매매 락 획득. 성공 시 lock_token, 실패 시 None."""
    r = get_redis()
    key = f"lock:trade:{user_id}"
    token = uuid.uuid4().hex
    acquired = await r.set(key, token, nx=True, ex=ttl)
    if acquired:
        logger.debug("락 획득: %s (ttl=%ds)", key, ttl)
        return token
    logger.debug("락 획득 실패: %s (이미 잠김)", key)
    return None


async def release_trade_lock(
    user_id: int | str,
    lock_token: str,
) -> bool:
    """락 해제. Lua 스크립트로 소유자 확인 후 DEL."""
    r = get_redis()
    key = f"lock:trade:{user_id}"
    result = await r.eval(_RELEASE_LUA, 1, key, lock_token)
    released = bool(result)
    if released:
        logger.debug("락 해제: %s", key)
    else:
        logger.warning("락 해제 실패 (소유자 불일치): %s", key)
    return released

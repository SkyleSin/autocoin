"""Redis 기반 유저별 슬라이딩 윈도우 레이트 리미터 (Lua 원자적)."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from infra.redis_client import get_redis

logger = logging.getLogger(__name__)

# Lua 스크립트: 슬라이딩 윈도우 체크 + 원자적 추가/거부
# KEYS[1] = rate_limit:{user_id}
# ARGV[1] = now, ARGV[2] = cutoff, ARGV[3] = max_requests
# ARGV[4] = member (unique id), ARGV[5] = expire_sec
# 반환: 1 = 허용, 0 = 초과
_RATE_LIMIT_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[2])
local count = redis.call('ZCARD', KEYS[1])
if count < tonumber(ARGV[3]) then
    redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
    return 1
end
return 0
"""

_script_sha: str | None = None


async def _get_script_sha() -> str:
    global _script_sha
    if _script_sha is None:
        r = get_redis()
        _script_sha = await r.script_load(_RATE_LIMIT_LUA)
    return _script_sha


async def check_rate_limit(
    user_id: int | str,
    max_requests: int = 15,
    window_sec: float = 1.0,
) -> bool:
    """유저별 레이트 리밋 체크. True면 허용, False면 초과."""
    r = get_redis()
    key = f"rate_limit:{user_id}"
    now = time.time()
    cutoff = now - window_sec
    member = f"{now}:{uuid.uuid4().hex[:8]}"
    expire = int(window_sec) + 2

    sha = await _get_script_sha()
    try:
        result = await r.evalsha(
            sha, 1, key, str(now), str(cutoff),
            str(max_requests), member, str(expire),
        )
    except Exception:
        # 스크립트 캐시 미스 시 재로드
        global _script_sha
        _script_sha = None
        sha = await _get_script_sha()
        result = await r.evalsha(
            sha, 1, key, str(now), str(cutoff),
            str(max_requests), member, str(expire),
        )
    return result == 1


async def wait_for_rate_limit(
    user_id: int | str,
    max_requests: int = 15,
    window_sec: float = 1.0,
) -> None:
    """레이트 리밋 여유가 생길 때까지 대기."""
    while not await check_rate_limit(user_id, max_requests, window_sec):
        logger.debug("rate_limit 대기: user=%s", user_id)
        await asyncio.sleep(0.1)

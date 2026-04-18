"""Redis 기반 매매 Job 큐 — List(즉시) + Sorted Set(지연)."""

from __future__ import annotations

import json
import logging
import time

from infra.redis_client import get_redis

logger = logging.getLogger(__name__)

TRADE_QUEUE_KEY = "trade_jobs"
DELAYED_QUEUE_KEY = "delayed_jobs"


async def push_trade_job(job_data: dict) -> None:
    """Redis List에 매매 Job push (LPUSH)."""
    r = get_redis()
    payload = json.dumps(job_data, ensure_ascii=False)
    await r.lpush(TRADE_QUEUE_KEY, payload)
    logger.debug("push_trade_job: %s", job_data.get("type"))


async def pop_trade_job(timeout: int = 5) -> dict | None:
    """Redis List에서 매매 Job pop (BRPOP). timeout초 대기."""
    r = get_redis()
    result = await r.brpop(TRADE_QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    _, raw = result
    data: dict = json.loads(raw)
    logger.debug("pop_trade_job: %s", data.get("type"))
    return data


async def push_delayed_job(job_data: dict, execute_at: float) -> None:
    """Redis Sorted Set에 지연 Job 추가. score=execute_at(epoch)."""
    r = get_redis()
    payload = json.dumps(job_data, ensure_ascii=False)
    await r.zadd(DELAYED_QUEUE_KEY, {payload: execute_at})
    delay = max(0, execute_at - time.time())
    logger.debug("push_delayed_job: %s (%.0fs 후)", job_data.get("type"), delay)


# Lua 스크립트: score <= now 인 항목을 원자적으로 pop
_POLL_DELAYED_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local items = redis.call('ZRANGEBYSCORE', key, '-inf', now, 'LIMIT', 0, 50)
if #items > 0 then
    for _, v in ipairs(items) do
        redis.call('ZREM', key, v)
    end
end
return items
"""


async def poll_delayed_jobs() -> list[dict]:
    """실행 시각 도달한 지연 Job들을 원자적으로 pop."""
    r = get_redis()
    now = time.time()
    items: list[str] = await r.eval(_POLL_DELAYED_LUA, 1, DELAYED_QUEUE_KEY, str(now))
    if not items:
        return []
    result = [json.loads(raw) for raw in items]
    logger.debug("poll_delayed_jobs: %d건 pop", len(result))
    return result

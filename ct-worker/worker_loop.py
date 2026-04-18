"""ct-worker 메인 루프 — Redis 큐에서 Job pop → 처리."""

from __future__ import annotations

import asyncio
import logging

from handlers.candle import handle_collect_candles
from infra.api.user import get_user_full
from infra.redis_queue import poll_delayed_jobs, pop_trade_job, push_trade_job
from infra.redis_rate_limit import wait_for_rate_limit
from utils.config import Settings
from utils.user_utils import get_user_settings_from_dict

logger = logging.getLogger(__name__)


async def trade_worker(settings: Settings, shutdown: asyncio.Event, name: str) -> None:
    """Redis 큐에서 Job pop → 타입별 핸들러 실행."""
    log = logging.getLogger(name)
    while not shutdown.is_set():
        job_data = await pop_trade_job(timeout=2)
        if job_data is None:
            continue

        job_type = job_data.get("type")
        user_id = job_data.get("user_id", "master")

        try:
            user_settings = await _resolve_settings(settings, user_id)
            if user_settings is None:
                log.warning("유저 없음: %s — Job 무시", user_id)
                continue

            await wait_for_rate_limit(user_id)

            if job_type == "collect_candles":
                await handle_collect_candles(job_data.get("payload", {}), user_settings)
            else:
                # TODO: 주문 흐름 설계 후 핸들러 연결
                log.warning("Job 핸들러 미구현: %s", job_type)

        except Exception:
            log.exception("Job 처리 실패: %s", job_data)


async def delayed_job_poller(shutdown: asyncio.Event) -> None:
    """지연 큐에서 실행 시각 도달한 Job을 trade_jobs로 이동."""
    while not shutdown.is_set():
        try:
            jobs = await poll_delayed_jobs()
            for job_data in jobs:
                await push_trade_job(job_data)
        except Exception:
            logger.exception("delayed_job_poller 오류")
        await asyncio.sleep(1)


async def _resolve_settings(settings: Settings, user_id: str) -> Settings | None:
    if user_id == "master":
        return settings
    user_data = await get_user_full(int(user_id))
    if user_data is None:
        return None
    return get_user_settings_from_dict(user_data, settings)

"""ct-worker 워커 루프 — 매매 큐 소비 + 지연 큐 폴링."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from infra.api.user import get_user_full
from infra.redis_lock import acquire_trade_lock, release_trade_lock
from infra.redis_queue import poll_delayed_jobs, pop_trade_job, push_trade_job
from infra.redis_rate_limit import wait_for_rate_limit
from job_runner.handlers.trade import _cancel_order_sync
from job_runner.handlers.trade_confirm import confirm_trade
from job_runner.handlers.trade_redis import execute_trade
from utils.config import Settings
from utils.user_utils import get_user_settings_from_dict

logger = logging.getLogger(__name__)


async def trade_worker(
    settings: Settings,
    shutdown: asyncio.Event,
    name: str,
) -> None:
    """매매 큐에서 Job pop -> 처리."""
    log = logging.getLogger(name)
    while not shutdown.is_set():
        job_data = await pop_trade_job(timeout=2)
        if job_data is None:
            continue

        job_type = job_data.get("type")
        user_id = job_data.get("user_id", "master")

        try:
            user_settings = await _resolve_user_settings(settings, user_id)
            if user_settings is None:
                log.warning("유저 없음: %s — Job 무시", user_id)
                continue

            await wait_for_rate_limit(user_id)
            if job_type == "execute_trade":
                await _handle_with_lock(user_settings, job_data, user_id)
            elif job_type == "confirm_trade":
                await confirm_trade(user_settings, job_data)
            elif job_type == "cancel_order":
                await _handle_cancel(user_settings, job_data)
            else:
                log.warning("알 수 없는 Job 타입: %s", job_type)
        except Exception:
            log.exception("Job 처리 실패: %s", job_data)


async def _resolve_user_settings(
    settings: Settings,
    user_id: str,
) -> Settings | None:
    """user_id로 유저별 Settings 생성. 'master'면 기본 settings 반환."""
    if user_id == "master":
        return settings
    user_data = await get_user_full(int(user_id))
    if user_data is None:
        return None
    return get_user_settings_from_dict(user_data, settings)


async def _handle_with_lock(
    settings: Settings,
    job_data: dict[str, Any],
    user_id: str,
) -> None:
    """매매 락 획득 후 execute_trade 실행."""
    lock_token = await acquire_trade_lock(user_id)
    if lock_token is None:
        await push_trade_job(job_data)
        await asyncio.sleep(0.5)
        return
    try:
        await execute_trade(settings, job_data)
    finally:
        await release_trade_lock(user_id, lock_token)


async def _handle_cancel(
    settings: Settings,
    job_data: dict[str, Any],
) -> None:
    """주문 취소 처리."""
    order_uuid = str(
        job_data.get("uuid", "")
        or job_data.get("content", {}).get("uuid", "")
    )
    if order_uuid:
        await asyncio.to_thread(_cancel_order_sync, settings, order_uuid)
        logger.info("주문 취소 완료: %s", order_uuid[:8])


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

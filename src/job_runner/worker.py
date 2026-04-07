"""`PriorityQueue`에서 `(priority, job_id)`를 꺼내 DB 잡을 처리한다."""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_runner.exceptions import JobExecutionFailed
from job_runner.handlers.analysis import handle_analyze_local_llm, handle_analyze_technical
from job_runner.handlers.bithumb import (
    handle_bithumb_markets,
    handle_bithumb_orders_chance,
    handle_bithumb_ticker,
)
from job_runner.handlers.candle import handle_collect_candles
from job_runner.handlers.trade import (
    handle_cancel_order,
    handle_check_balance,
    handle_check_order,
    handle_place_order,
)
from job_runner.poller import JobQueueItem, is_shutdown_queue_item
from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from job_runner.workers import CoinServerAPIError
from models.job import Job
from models.job_status import JobStatus
from utils.config import Settings

logger = logging.getLogger(__name__)


def _should_retry_as_fail(exc: BaseException) -> bool:
    """네트워크·거래소 일시 오류 등 → `Failed`(재시도) 또는 `retry_max` 초과 시 `Error`."""
    return isinstance(
        exc,
        (
            JobExecutionFailed,
            CoinServerAPIError,
            httpx.RequestError,
            TimeoutError,
            ConnectionError,
            OSError,
        ),
    )


async def _execute_job(
    job: Job,
    settings: Settings,
    *,
    api_rate_limiter: AsyncSlidingWindowRateLimiter | None,
    session: AsyncSession | None = None,
) -> None:
    """Job Type에 따라 적절한 핸들러로 디스패치."""
    rl = api_rate_limiter

    if job.type == "noop":
        job.status = JobStatus.Complete.value
        job.response = {"ok": True}
    elif job.type == "bithumb_markets":
        await handle_bithumb_markets(job, settings, rate_limiter=rl)
    elif job.type == "bithumb_ticker":
        await handle_bithumb_ticker(job, settings, rate_limiter=rl)
    elif job.type == "bithumb_orders_chance":
        await handle_bithumb_orders_chance(job, settings, rate_limiter=rl)
    elif job.type == "collect_candles":
        await handle_collect_candles(job, settings, rate_limiter=rl, session=session)
    elif job.type == "analyze_technical":
        if session is None:
            raise ValueError("analyze_technical 잡은 DB 세션이 필요합니다.")
        await handle_analyze_technical(job, settings, session=session)
    elif job.type == "analyze_local_llm":
        if session is None:
            raise ValueError("analyze_local_llm 잡은 DB 세션이 필요합니다.")
        await handle_analyze_local_llm(job, settings, session=session)
    elif job.type == "check_balance":
        await handle_check_balance(job, settings, rate_limiter=rl, session=session)
    elif job.type == "place_order":
        await handle_place_order(job, settings, rate_limiter=rl, session=session)
    elif job.type == "check_order":
        await handle_check_order(job, settings, rate_limiter=rl, session=session)
    elif job.type == "cancel_order":
        await handle_cancel_order(job, settings, rate_limiter=rl, session=session)
    else:
        raise ValueError(f"지원하지 않는 job type: {job.type}")


async def worker(
    queue: asyncio.PriorityQueue[JobQueueItem],
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    name: str,
    *,
    api_rate_limiter: AsyncSlidingWindowRateLimiter | None = None,
) -> None:
    try:
        while True:
            item: JobQueueItem = await queue.get()
            try:
                if is_shutdown_queue_item(item):
                    logger.info("%s: got shutdown sentinel, exit", name)
                    break
                _priority, job_id = item
                logger.info(
                    "%s: dequeued priority=%s job_id=%s qsize~=%s",
                    name,
                    _priority,
                    job_id,
                    queue.qsize(),
                )
                async with session_factory() as session:
                    job = await session.get(Job, job_id)
                    if job is None:
                        logger.warning("%s: job %s not found (skip)", name, job_id)
                    else:
                        if job.status != JobStatus.Queue.value:
                            logger.warning(
                                "%s: job_id=%s expected status Queue, got %s",
                                name,
                                job_id,
                                job.status,
                            )
                        job.status = JobStatus.Running.value
                        await session.commit()
                        logger.info(
                            "%s: execute start job_id=%s type=%s (status=Running)",
                            name,
                            job_id,
                            job.type,
                        )
                        try:
                            await _execute_job(
                                job,
                                settings,
                                api_rate_limiter=api_rate_limiter,
                                session=session,
                            )
                            await session.commit()
                            logger.info(
                                "%s: execute ok job_id=%s type=%s -> %s",
                                name,
                                job_id,
                                job.type,
                                job.status,
                            )
                        except Exception as e:
                            logger.exception("%s: job %s failed", name, job_id)
                            await session.rollback()
                            row = await session.get(Job, job_id)
                            if row is not None:
                                msg = str(e)[:2000]
                                if _should_retry_as_fail(e):
                                    next_cnt = row.retry_cnt + 1
                                    row.retry_cnt = next_cnt
                                    row.error_message = msg
                                    if next_cnt >= settings.job_retry_max:
                                        row.status = JobStatus.Error.value
                                    else:
                                        row.status = JobStatus.Failed.value
                                else:
                                    row.status = JobStatus.Error.value
                                    row.error_message = msg
                                await session.commit()
                                logger.info(
                                    "%s: execute fail job_id=%s -> %s retry_cnt=%s (%s)",
                                    name,
                                    job_id,
                                    row.status,
                                    row.retry_cnt,
                                    type(e).__name__,
                                )
            finally:
                queue.task_done()
    finally:
        logger.info("%s stopped", name)

"""ct-scheduler: 수집 + Safety 모니터 + 비-매매 Job 독립 프로세스."""

from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infra.api_client import close_api_client, init_api_client
from infra.redis_client import close_redis, init_redis
from job_runner.poller import SHUTDOWN_QUEUE_ITEM, JobQueueItem, poller
from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from job_runner.worker import worker as job_worker
from utils.config import Settings
from utils.logging_setup import setup_logging

logger = logging.getLogger("ct-scheduler")


def _build_rate_limiter(settings: Settings) -> AsyncSlidingWindowRateLimiter | None:
    if settings.bithumb_max_requests_per_sec > 0:
        return AsyncSlidingWindowRateLimiter(
            settings.bithumb_max_requests_per_sec, window_seconds=1.0,
        )
    return None


async def _shutdown_workers(
    job_queue: asyncio.PriorityQueue[JobQueueItem],
    worker_tasks: list[asyncio.Task[None]],
    count: int,
) -> None:
    await job_queue.join()
    for _ in range(count):
        await job_queue.put(SHUTDOWN_QUEUE_ITEM)
    await asyncio.gather(*worker_tasks, return_exceptions=True)


async def main() -> None:
    """스케줄러 메인 — 수집/분석/안전장치 + 비-매매 Job."""
    settings = Settings()
    setup_logging(settings=settings)

    if not settings.database_url.strip():
        raise ValueError("DATABASE_URL이 필요합니다.")

    engine = create_async_engine(settings.database_url.strip())
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await init_redis(settings.redis_url)
    await init_api_client(settings.api_base_url)

    rate_limiter = _build_rate_limiter(settings)
    shutdown = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    # Job poller + worker
    jq: asyncio.PriorityQueue[JobQueueItem] = asyncio.PriorityQueue()
    poller_t = asyncio.create_task(poller(jq, sf, settings), name="job-poller")
    worker_ts = [
        asyncio.create_task(
            job_worker(jq, sf, settings, f"w-{i}", api_rate_limiter=rate_limiter),
            name=f"job-worker-{i}",
        )
        for i in range(settings.job_worker_count)
    ]

    from core.collector.collect_scheduler import collect_scheduler
    from core.safety.monitor import safety_monitor
    from core.safety.shock_monitor import shock_monitor

    sched_t = asyncio.create_task(collect_scheduler(sf, settings), name="collect")
    safety_t = asyncio.create_task(safety_monitor(sf, settings), name="safety")
    shock_t = asyncio.create_task(shock_monitor(sf, settings), name="shock")

    logger.info("ct-scheduler 시작: workers=%s", settings.job_worker_count)
    await shutdown.wait()
    logger.info("ct-scheduler 종료 중...")

    for t in (shock_t, safety_t, sched_t, poller_t):
        t.cancel()
    await asyncio.gather(
        shock_t, safety_t, sched_t, poller_t, return_exceptions=True,
    )
    await _shutdown_workers(jq, worker_ts, settings.job_worker_count)

    await close_api_client()
    await close_redis()
    await engine.dispose()
    logger.info("ct-scheduler 종료 완료")


if __name__ == "__main__":
    asyncio.run(main())

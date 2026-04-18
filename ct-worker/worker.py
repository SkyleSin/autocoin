"""ct-worker: 빗썸 API 전담 워커."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from infra.api_client import close_api_client, init_api_client
from infra.redis_client import close_redis, init_redis
from utils.config import Settings
from utils.logging_setup import setup_logging
from worker_loop import delayed_job_poller, trade_worker

logger = logging.getLogger("ct-worker")


async def main() -> None:
    settings = Settings()
    setup_logging(settings=settings)

    await init_redis(settings.redis_url)
    await init_api_client(settings.api_base_url)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    worker_count = int(os.environ.get("WORKER_CONCURRENCY", "20"))
    logger.info("ct-worker 시작: workers=%d", worker_count)

    tasks = [
        asyncio.create_task(
            trade_worker(settings, shutdown, f"w-{i}"),
            name=f"trade-worker-{i}",
        )
        for i in range(worker_count)
    ]
    tasks.append(asyncio.create_task(delayed_job_poller(shutdown), name="delayed-poller"))

    await shutdown.wait()
    logger.info("ct-worker 종료 중...")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    await close_api_client()
    await close_redis()
    logger.info("ct-worker 종료 완료")


if __name__ == "__main__":
    asyncio.run(main())

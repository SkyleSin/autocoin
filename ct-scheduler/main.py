"""ct-scheduler: 시간 기반 트리거 전용."""

from __future__ import annotations

import asyncio
import logging
import signal

from infra.api_client import close_api_client, init_api_client
from infra.redis_client import close_redis, init_redis
from utils.config import Settings
from utils.logging_setup import setup_logging

logger = logging.getLogger("ct-scheduler")


async def main() -> None:
    settings = Settings()
    setup_logging(settings=settings)

    await init_redis(settings.redis_url)
    await init_api_client(settings.api_base_url)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    from collect_scheduler import collect_scheduler

    task = asyncio.create_task(collect_scheduler(settings), name="collect")

    logger.info("ct-scheduler 시작")
    await shutdown.wait()
    logger.info("ct-scheduler 종료 중...")

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    await close_api_client()
    await close_redis()
    logger.info("ct-scheduler 종료 완료")


if __name__ == "__main__":
    asyncio.run(main())

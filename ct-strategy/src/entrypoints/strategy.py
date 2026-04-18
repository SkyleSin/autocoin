"""ct-strategy: 전략 평가 독립 프로세스.

Redis Pub/Sub(analysis_ready)에서 이벤트를 수신하여 전략을 실행한다.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from infra.api_client import close_api_client, init_api_client
from infra.redis_client import close_redis, init_redis
from utils.config import Settings
from utils.logging_setup import setup_logging

logger = logging.getLogger("ct-strategy")


async def main() -> None:
    """전략 프로세스 메인 — Redis Pub/Sub 기반 이벤트 구동."""
    settings = Settings()
    setup_logging(settings=settings)

    await init_redis(settings.redis_url)
    await init_api_client(settings.api_base_url)

    shutdown = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("종료 시그널 수신")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("ct-strategy 시작")

    from core.strategy.strategy_scheduler import strategy_scheduler

    task = asyncio.create_task(
        strategy_scheduler(settings),
        name="strategy-scheduler",
    )

    await shutdown.wait()
    logger.info("ct-strategy 종료 중...")

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    await close_api_client()
    await close_redis()
    logger.info("ct-strategy 종료 완료")


if __name__ == "__main__":
    asyncio.run(main())

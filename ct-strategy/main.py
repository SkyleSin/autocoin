"""ct-strategy: 분석 + 전략 평가 + safety/shock 감시.

지표 계산, 전략 평가, 안전장치 판단을 담당한다.
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
    settings = Settings()
    setup_logging(settings=settings)

    await init_redis(settings.redis_url)
    await init_api_client(settings.api_base_url)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    from analysis_loop import analysis_loop
    from monitoring.monitor import safety_monitor
    from monitoring.shock_monitor import shock_monitor

    tasks = [
        asyncio.create_task(analysis_loop(settings), name="analysis"),
        asyncio.create_task(safety_monitor(settings), name="safety"),
        asyncio.create_task(shock_monitor(settings), name="shock"),
    ]

    logger.info("ct-strategy 시작 (analysis + safety + shock)")
    await shutdown.wait()
    logger.info("ct-strategy 종료 중...")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    await close_api_client()
    await close_redis()
    logger.info("ct-strategy 종료 완료")


if __name__ == "__main__":
    asyncio.run(main())

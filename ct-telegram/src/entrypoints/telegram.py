"""ct-telegram: 텔레그램 봇 독립 프로세스."""

from __future__ import annotations

import asyncio
import logging
import signal

from infra.api_client import close_api_client, init_api_client
from infra.redis_client import close_redis, init_redis
from utils.config import Settings
from utils.logging_setup import setup_logging

logger = logging.getLogger("ct-telegram")


async def main() -> None:
    """텔레그램 프로세스 메인."""
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

    logger.info("ct-telegram 시작")

    from entrypoints.telegram_subscriber import send_message_subscriber
    from telegram.poller import telegram_poller

    tasks = [
        asyncio.create_task(
            telegram_poller(settings), name="tg-poller",
        ),
        asyncio.create_task(
            send_message_subscriber(settings, shutdown), name="tg-sub",
        ),
    ]

    await shutdown.wait()
    logger.info("ct-telegram 종료 중...")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    await close_api_client()
    await close_redis()
    logger.info("ct-telegram 종료 완료")


if __name__ == "__main__":
    asyncio.run(main())

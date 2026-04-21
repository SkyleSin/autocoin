"""텔레그램 getUpdates 폴링 루프."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
from utils.config import Settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

UpdateHandler = Callable[[dict[str, Any], Settings], Coroutine[Any, Any, None]]


async def _poll_loop(
    token: str,
    settings: Settings,
    handler: UpdateHandler,
    name: str = "poller",
) -> None:
    """범용 getUpdates 폴링 루프."""
    url = f"{TELEGRAM_API}/bot{token}/getUpdates"
    offset = 0

    logger.info("%s: 시작", name)

    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            while True:
                try:
                    resp = await client.get(
                        url, params={"offset": offset, "timeout": 30},
                    )
                    if not resp.is_success:
                        logger.warning("%s: status=%s", name, resp.status_code)
                        await asyncio.sleep(5)
                        continue

                    for update in resp.json().get("result", []):
                        update_id = update.get("update_id", 0)
                        offset = max(offset, update_id + 1)
                        try:
                            await handler(update, settings)
                        except Exception:
                            logger.exception("%s: handle error %s", name, update_id)

                except asyncio.CancelledError:
                    raise
                except httpx.ReadTimeout:
                    continue
                except Exception:
                    logger.exception("%s: poll error", name)
                    await asyncio.sleep(5)
    finally:
        logger.info("%s: 종료", name)


async def telegram_poller(settings: Settings) -> None:
    """유저 봇 폴링."""
    if not settings.telegram_bot_token:
        logger.info("telegram_poller: 토큰 미설정, 비활성화")
        return

    from telegram.handler import handle_update
    await _poll_loop(settings.telegram_bot_token, settings, handle_update, "tg-user")


async def master_poller(settings: Settings) -> None:
    """마스터 봇 폴링."""
    if not settings.master_bot_token:
        logger.info("master_poller: 토큰 미설정, 비활성화")
        return

    from telegram.handler_master import handle_master_update
    await _poll_loop(settings.master_bot_token, settings, handle_master_update, "tg-master")

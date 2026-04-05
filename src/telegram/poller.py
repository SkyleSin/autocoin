"""텔레그램 getUpdates 폴링 루프."""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram.handler import handle_update
from utils.config import Settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


async def telegram_poller(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """텔레그램 업데이트를 폴링하는 무한 루프."""
    if not settings.telegram_bot_token:
        logger.info("telegram_poller: 토큰 미설정, 비활성화")
        return

    token = settings.telegram_bot_token
    url = f"{TELEGRAM_API}/bot{token}/getUpdates"
    offset = 0

    logger.info("telegram_poller: started")

    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            while True:
                try:
                    resp = await client.get(
                        url,
                        params={
                            "offset": offset,
                            "timeout": 30,  # long polling
                        },
                    )
                    if not resp.is_success:
                        logger.warning(
                            "telegram_poller: status=%s", resp.status_code,
                        )
                        await asyncio.sleep(5)
                        continue

                    data = resp.json()
                    updates = data.get("result", [])

                    for update in updates:
                        update_id = update.get("update_id", 0)
                        offset = max(offset, update_id + 1)
                        try:
                            await handle_update(update, session_factory, settings)
                        except Exception:
                            logger.exception(
                                "telegram_poller: handle error update_id=%s",
                                update_id,
                            )

                except asyncio.CancelledError:
                    raise
                except httpx.ReadTimeout:
                    # long polling 타임아웃은 정상
                    continue
                except Exception:
                    logger.exception("telegram_poller: poll error")
                    await asyncio.sleep(5)
    finally:
        logger.info("telegram_poller stopped")

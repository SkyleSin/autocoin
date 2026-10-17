"""마켓 목록 동기화 핸들러 — 빗썸 마켓 → ct-api 동기화."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bithumb.market import BithumbMarket
from infra.api_client import api_put
from utils.config import Settings

logger = logging.getLogger(__name__)


def _fetch_sync(settings: Settings) -> list[dict[str, Any]]:
    client = BithumbMarket(settings)
    try:
        return client.get_all()
    finally:
        client.close()


async def handle_sync_markets(payload: dict, settings: Settings) -> None:
    """빗썸 마켓 목록 조회 → ct-api PUT /markets/sync."""
    markets = await asyncio.to_thread(_fetch_sync, settings)
    if not markets:
        logger.warning("sync_markets: 마켓 조회 결과 없음")
        return

    # KRW 마켓만 필터
    items = [
        {
            "market": m["market"],
            "korean_name": m.get("korean_name", ""),
            "english_name": m.get("english_name", ""),
        }
        for m in markets
        if str(m.get("market", "")).startswith("KRW-")
    ]

    resp = await api_put("/api/markets/sync", json={"items": items})

    if resp and resp.get("upserted") is not None:
        logger.info(
            "sync_markets: upserted=%d deactivated=%d",
            resp["upserted"], resp["deactivated"],
        )
    else:
        logger.warning("sync_markets: API 응답 이상: %s", resp)

"""포트폴리오 동기화 핸들러 — 빗썸 잔고 → ct-api 동기화."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bithumb.account import BithumbAccount
from infra.api_client import api_put
from utils.config import Settings

logger = logging.getLogger(__name__)


def _fetch_sync(settings: Settings) -> list[dict[str, Any]]:
    client = BithumbAccount(settings)
    try:
        return client.get_balances()
    finally:
        client.close()


async def handle_sync_portfolio(
    payload: dict, settings: Settings,
) -> None:
    """빗썸 잔고 조회 → ct-api PUT /portfolios/sync.

    payload:
      - user_id (int): 대상 유저
    """
    user_id = payload.get("user_id")
    if user_id is None:
        logger.warning("sync_portfolio: user_id 없음")
        return

    accounts = await asyncio.to_thread(_fetch_sync, settings)
    if not accounts:
        logger.warning("sync_portfolio: user=%s 잔고 조회 결과 없음", user_id)
        return

    items = []
    for acc in accounts:
        balance = float(acc.get("balance", 0))
        locked = float(acc.get("locked", 0))
        if balance > 0 or locked > 0:
            items.append({
                "currency": acc.get("currency", ""),
                "balance": str(balance),
                "locked": str(locked),
                "avg_buy_price": str(float(acc.get("avg_buy_price", 0))),
            })

    resp = await api_put("/api/portfolios/sync", json={
        "user_id": user_id,
        "items": items,
    })

    if resp and resp.get("upserted") is not None:
        logger.info(
            "sync_portfolio: user=%s upserted=%d removed=%d",
            user_id, resp["upserted"], resp["removed"],
        )
    else:
        logger.warning("sync_portfolio: user=%s API 응답 이상: %s", user_id, resp)

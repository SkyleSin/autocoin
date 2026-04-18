"""Redis 큐 기반 매매 핸들러 — execute_trade.

ct-worker에서 사용. DB 접근 없이 ct-api REST 호출로 동작.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from infra.redis_pubsub import publish_telegram
from infra.redis_queue import push_delayed_job
from job_runner.handlers.trade import _accounts_sync, _place_order_sync
from job_runner.handlers.trade_balance import chain_trade_from_balance
from utils.config import Settings

logger = logging.getLogger(__name__)


async def execute_trade(
    settings: Settings,
    job_data: dict[str, Any],
) -> None:
    """check_balance + place_order 통합. Redis 큐 경로."""
    content = job_data.get("content", {})
    market = str(content.get("market", ""))

    accounts = await asyncio.to_thread(_accounts_sync, settings)

    order_content = await chain_trade_from_balance(
        settings, accounts, content,
    )
    if order_content is None:
        return

    data = await asyncio.to_thread(
        _place_order_sync, settings,
        order_content["market"], order_content["side"],
        volume=order_content.get("volume"),
        price=order_content.get("price"),
        ord_type=order_content.get("ord_type", "limit"),
    )

    order_uuid = data.get("uuid", "")
    side_ko = "매수" if order_content["side"] == "bid" else "매도"
    await publish_telegram(
        settings,
        f"📝 `{market}` {side_ko} 주문 접수\n"
        f"주문 ID: `{order_uuid}`\n"
        f"상태: `{data.get('state', '')}`\n30초 후 체결 확인",
    )

    if order_uuid:
        await push_delayed_job(
            {
                "type": "confirm_trade",
                "content": {
                    "uuid": order_uuid,
                    "market": market,
                    "side": order_content["side"],
                    "ord_type": order_content.get("ord_type", ""),
                    "retry_count": 0,
                },
            },
            time.time() + 30,
        )

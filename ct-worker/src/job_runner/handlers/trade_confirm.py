"""체결 확인 핸들러 — Redis 큐 경로용 confirm_trade."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from infra.api.trade import create_trade
from infra.redis_pubsub import publish_telegram
from infra.redis_queue import push_delayed_job
from job_runner.handlers.trade import _get_order_sync
from utils.config import Settings

logger = logging.getLogger(__name__)


async def confirm_trade(
    settings: Settings,
    job_data: dict[str, Any],
) -> None:
    """체결 확인. 미체결 시 재확인 지연 Job 등록 (최대 2회)."""
    content = job_data.get("content", {})
    order_uuid = str(content.get("uuid", ""))
    market = str(content.get("market", ""))
    side = str(content.get("side", ""))
    retry_count = int(content.get("retry_count", 0))
    data = await asyncio.to_thread(_get_order_sync, settings, order_uuid)
    state = str(data.get("state", ""))
    side_ko = "매수" if side == "bid" else "매도"
    if state == "done":
        await _record_trade(data, market, side, order_uuid)
        vol = data.get("executed_volume", "?")
        fee = data.get("paid_fee", "?")
        await publish_telegram(
            settings,
            f"✅ `{market}` {side_ko} 체결 완료\n"
            f"체결 수량: `{vol}`\n수수료: `{fee}`\n"
            f"주문 ID: `{order_uuid}`",
        )
    elif state in ("wait", "watch"):
        await _handle_pending(
            settings, content, retry_count, market, side_ko, data,
        )
    elif state == "cancel":
        await publish_telegram(
            settings,
            f"❌ `{market}` {side_ko} 주문 취소됨\n"
            f"주문 ID: `{order_uuid}`",
        )


async def _record_trade(
    data: dict, market: str, side: str, order_uuid: str,
) -> None:
    """체결 완료 -> Trade API 기록."""
    vol_str = data.get("executed_volume", "0")
    trades_list = data.get("trades", [])
    avg_price = 0.0
    if trades_list:
        tf = sum(float(t.get("funds", 0)) for t in trades_list)
        tv = sum(float(t.get("volume", 0)) for t in trades_list)
        avg_price = tf / tv if tv > 0 else 0
    await create_trade({
        "market": market,
        "side": "buy" if side == "bid" else "sell",
        "volume": str(vol_str),
        "price_krw": str(round(avg_price, 4)) if avg_price else None,
        "status": "done",
        "exchange_order_id": order_uuid,
        "payload": data,
    })
    logger.info("Trade 기록: %s %s vol=%s", market, side, vol_str)


async def _handle_pending(
    settings: Settings, content: dict, retry_count: int,
    market: str, side_ko: str, data: dict,
) -> None:
    """미체결 처리 — 재확인 또는 사용자 알림."""
    order_uuid = str(content.get("uuid", ""))
    if retry_count < 2:
        new_content = {**content, "retry_count": retry_count + 1}
        await push_delayed_job(
            {"type": "confirm_trade", "content": new_content},
            time.time() + 30,
        )
        logger.info("미체결 재확인: %s (retry %d/2)", market, retry_count + 1)
    else:
        remaining = data.get("remaining_volume", "?")
        await publish_telegram(
            settings,
            f"⚠️ `{market}` {side_ko} 미체결\n"
            f"잔량: `{remaining}`\n주문 ID: `{order_uuid}`",
        )

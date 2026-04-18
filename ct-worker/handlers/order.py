"""confirm_order / cancel_order 핸들러."""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any

from handlers.bithumb_api import BithumbAPIError, cancel_order, get_order
from infra.api.signal import get_latest_signal
from infra.api.trade import create_trade
from infra.redis_pubsub import publish_telegram
from infra.redis_queue import push_delayed_job, push_trade_job
from utils.config import Settings

logger = logging.getLogger(__name__)

MAX_CONFIRM_RETRY = 2


async def handle_confirm_order(settings: Settings, job_data: dict[str, Any]) -> None:
    """주문 체결 확인. 미체결 시 최대 2회 재확인 후 유저 알림."""
    payload = job_data.get("payload", {})
    uuid = str(payload.get("uuid", ""))
    market = str(payload.get("market", ""))
    side = str(payload.get("side", ""))
    retry_count = int(payload.get("retry_count", 0))
    on_complete = payload.get("on_complete")
    chat_id = str(job_data.get("chat_id", settings.telegram_chat_id))

    if not uuid:
        logger.warning("confirm_order: uuid 없음")
        return

    try:
        data = await asyncio.to_thread(get_order, settings, uuid)
    except BithumbAPIError:
        logger.exception("주문 조회 실패: %s", uuid)
        return

    state = str(data.get("state", ""))
    side_ko = "매수" if side == "bid" else "매도"

    if state == "done":
        await _on_order_done(settings, data, market, side, side_ko, uuid, chat_id)
        if on_complete:
            await push_trade_job({
                "type": "check_account",
                "payload": on_complete,
                "chat_id": chat_id,
            })

    elif state in ("wait", "watch"):
        if retry_count < MAX_CONFIRM_RETRY:
            await push_delayed_job(
                {
                    "type": "confirm_order",
                    "payload": {**payload, "retry_count": retry_count + 1},
                    "chat_id": chat_id,
                },
                time.time() + 30,
            )
            logger.info("주문 미체결 재확인 예약: %s (retry %d)", uuid[:8], retry_count + 1)
        else:
            await _on_order_pending(settings, data, market, side_ko, uuid, chat_id)

    elif state == "cancel":
        await publish_telegram(
            chat_id,
            f"❌ `{market}` {side_ko} 주문 취소됨\n주문 ID: `{uuid}`",
        )


async def handle_cancel_order(settings: Settings, job_data: dict[str, Any]) -> None:
    """주문 취소."""
    payload = job_data.get("payload", {})
    uuid = str(payload.get("uuid", ""))
    market = str(payload.get("market", ""))
    side = str(payload.get("side", ""))
    chat_id = str(job_data.get("chat_id", settings.telegram_chat_id))
    side_ko = "매수" if side == "bid" else "매도"

    if not uuid:
        logger.warning("cancel_order: uuid 없음")
        return

    try:
        await asyncio.to_thread(cancel_order, settings, uuid)
        await publish_telegram(
            chat_id,
            f"✅ `{market}` {side_ko} 주문 취소 완료\n주문 ID: `{uuid}`",
        )
    except BithumbAPIError:
        logger.exception("주문 취소 실패: %s", uuid)
        await publish_telegram(
            chat_id,
            f"❌ 주문 취소 실패\n주문 ID: `{uuid}`\n이미 체결되었을 수 있습니다.",
        )


async def _on_order_done(
    settings: Settings,
    data: dict,
    market: str,
    side: str,
    side_ko: str,
    uuid: str,
    chat_id: str,
) -> None:
    """체결 완료 처리 — Trade 기록 + 텔레그램 알림."""
    trades_list = data.get("trades", [])
    executed_vol = data.get("executed_volume", "0")
    avg_price = 0.0
    if trades_list:
        total_funds = sum(float(t.get("funds", 0)) for t in trades_list)
        total_vol = sum(float(t.get("volume", 0)) for t in trades_list)
        avg_price = total_funds / total_vol if total_vol > 0 else 0.0

    # 최근 시그널 연결 (있으면)
    last_signal = await get_latest_signal(market)
    signal_id = last_signal.get("id") if last_signal else None

    try:
        await create_trade({
            "market": market,
            "side": "buy" if side == "bid" else "sell",
            "volume": str(executed_vol),
            "price_krw": str(round(avg_price, 4)) if avg_price else None,
            "status": "done",
            "exchange_order_id": uuid,
            "signal_id": signal_id,
            "payload": data,
        })
    except Exception:
        logger.warning("Trade 기록 실패: %s", uuid, exc_info=True)

    await publish_telegram(
        chat_id,
        f"✅ `{market}` {side_ko} 체결 완료\n"
        f"체결 수량: `{executed_vol}`\n"
        f"평균가: `{avg_price:,.2f}원`\n"
        f"수수료: `{data.get('paid_fee', '?')}`",
    )


async def _on_order_pending(
    settings: Settings,
    data: dict,
    market: str,
    side_ko: str,
    uuid: str,
    chat_id: str,
) -> None:
    """재확인 2회 후에도 미체결 — 유저에게 선택지 제공."""
    remaining = data.get("remaining_volume", "?")
    executed = data.get("executed_volume", "0")
    buttons = {
        "inline_keyboard": [[
            {"text": "🔄 취소 후 시장가 재주문",
             "callback_data": f"order_retry:{uuid}:{market}"},
            {"text": "⏭️ 대기 유지",
             "callback_data": f"order_keep:{uuid}"},
        ], [
            {"text": "❌ 취소만",
             "callback_data": f"order_cancel:{uuid}:{market}"},
        ]],
    }
    await publish_telegram(
        chat_id,
        f"⚠️ `{market}` {side_ko} 주문 미체결\n"
        f"체결: `{executed}` / 잔량: `{remaining}`\n"
        f"주문 ID: `{uuid}`",
        reply_markup=buttons,
    )

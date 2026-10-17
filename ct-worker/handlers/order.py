"""주문 핸들러 — place_order, check_order, cancel_order."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from bithumb.order import BithumbOrder
from infra.api.trade import create_trade
from infra.redis_pubsub import publish_to_telegram
from infra.redis_queue import push_delayed_job
from utils.config import Settings

logger = logging.getLogger(__name__)

# 체결 확인 지연 (초)
CHECK_DELAY_SEC = 30
# 최대 재확인 횟수
MAX_RETRY = 2


def _place_sync(
    settings: Settings, market: str, side: str,
    *, volume: str | None, price: str | None, ord_type: str,
) -> dict[str, Any]:
    client = BithumbOrder(settings)
    try:
        return client.place(market, side, volume=volume, price=price, ord_type=ord_type)
    finally:
        client.close()


def _get_sync(settings: Settings, uuid: str) -> dict[str, Any]:
    client = BithumbOrder(settings)
    try:
        return client.get(uuid)
    finally:
        client.close()


def _cancel_sync(settings: Settings, uuid: str) -> dict[str, Any]:
    client = BithumbOrder(settings)
    try:
        return client.cancel(uuid)
    finally:
        client.close()


async def handle_place_order(payload: dict, settings: Settings) -> None:
    """주문 실행 → 텔레그램 알림 → 30초 후 check_order 예약.

    payload:
      - market (str)
      - side (str): "bid" or "ask"
      - volume (str, optional): 매도 수량
      - price (str, optional): 매수 금액
      - ord_type (str): "price"(시장가 매수), "market"(시장가 매도), "limit"(지정가)
      - chat_id (str, optional)
    """
    market = payload.get("market", "")
    side = payload.get("side", "")
    volume = payload.get("volume")
    price = payload.get("price")
    ord_type = payload.get("ord_type", "limit")
    defense_reason = payload.get("defense_reason")
    chat_id = payload.get("chat_id", settings.telegram_chat_id)

    if not market or side not in ("bid", "ask"):
        logger.warning("place_order: 잘못된 파라미터 market=%s side=%s", market, side)
        return

    data = await asyncio.to_thread(
        _place_sync, settings, market, side,
        volume=volume, price=price, ord_type=ord_type,
    )

    order_uuid = data.get("uuid", "")
    order_state = data.get("state", "")
    side_ko = "매수" if side == "bid" else "매도"

    logger.info("place_order: %s %s uuid=%s state=%s", market, side_ko, order_uuid[:8], order_state)

    if chat_id:
        await publish_to_telegram({
            "type": "text",
            "chat_id": chat_id,
            "text": (
                f"📝 `{market}` {side_ko} 주문 접수\n"
                f"주문 ID: `{order_uuid}`\n"
                f"상태: `{order_state}`\n"
                f"{CHECK_DELAY_SEC}초 후 체결 확인 예정"
            ),
            "parse_mode": "Markdown",
        })

    # 30초 후 체결 확인 예약
    if order_uuid:
        check_payload: dict[str, Any] = {
            "uuid": order_uuid,
            "market": market,
            "side": side,
            "retry_count": 0,
            "chat_id": chat_id,
            "user_id": payload.get("user_id"),
        }
        if defense_reason:
            check_payload["defense_reason"] = defense_reason
        await push_delayed_job(
            {"type": "check_order", "payload": check_payload},
            execute_at=time.time() + CHECK_DELAY_SEC,
        )


async def handle_check_order(payload: dict, settings: Settings) -> None:
    """체결 확인 → 완료 시 Trade 기록, 미체결 시 재확인 또는 사용자 알림.

    payload:
      - uuid (str): 주문 ID
      - market (str)
      - side (str)
      - retry_count (int)
      - chat_id (str, optional)
    """
    order_uuid = payload.get("uuid", "")
    market = payload.get("market", "")
    side = payload.get("side", "")
    retry_count = payload.get("retry_count", 0)
    defense_reason = payload.get("defense_reason")
    chat_id = payload.get("chat_id", settings.telegram_chat_id)

    if not order_uuid:
        logger.warning("check_order: uuid 없음")
        return

    data = await asyncio.to_thread(_get_sync, settings, order_uuid)
    state = data.get("state", "")
    side_ko = "매수" if side == "bid" else "매도"

    if state == "done":
        # 체결 완료 → Trade 기록
        executed_vol = data.get("executed_volume", "0")
        trades_list = data.get("trades", [])
        avg_price = 0.0
        if trades_list:
            total_funds = sum(float(t.get("funds", 0)) for t in trades_list)
            total_vol = sum(float(t.get("volume", 0)) for t in trades_list)
            avg_price = total_funds / total_vol if total_vol > 0 else 0

        coin_currency = market.split("-")[1] if "-" in market else ""

        trade_data: dict[str, Any] = {
            "market": market,
            "side": "buy" if side == "bid" else "sell",
            "volume": executed_vol,
            "price_krw": str(round(avg_price, 4)) if avg_price else None,
            "status": "done",
            "exchange_order_id": order_uuid,
            "payload": data,
        }
        trade_user_id = payload.get("user_id")
        if trade_user_id:
            trade_data["user_id"] = int(trade_user_id)
        if defense_reason:
            trade_data["notes"] = f"defense:{defense_reason}"

        try:
            await create_trade(trade_data)
        except Exception:
            logger.exception("Trade 기록 실패: %s", order_uuid)

        logger.info("check_order: %s %s 체결 완료 vol=%s", market, side_ko, executed_vol)

        if chat_id:
            paid = data.get("paid_fee", "?")
            await publish_to_telegram({
                "type": "text",
                "chat_id": chat_id,
                "text": (
                    f"✅ `{market}` {side_ko} 체결 완료\n"
                    f"체결 수량: `{executed_vol}` {coin_currency}\n"
                    f"평균가: `{avg_price:,.0f}` KRW\n"
                    f"수수료: `{paid}`"
                ),
                "parse_mode": "Markdown",
            })

    elif state in ("wait", "watch"):
        if retry_count < MAX_RETRY:
            # 재확인 예약
            retry_payload: dict[str, Any] = {
                "uuid": order_uuid,
                "market": market,
                "side": side,
                "retry_count": retry_count + 1,
                "chat_id": chat_id,
            }
            if defense_reason:
                retry_payload["defense_reason"] = defense_reason
            await push_delayed_job(
                {"type": "check_order", "payload": retry_payload},
                execute_at=time.time() + CHECK_DELAY_SEC,
            )
            logger.info(
                "check_order: %s 미체결, 재확인 예약 (%d/%d)",
                market, retry_count + 1, MAX_RETRY,
            )
        elif chat_id:
            # 재확인 소진 → 사용자에게 선택권
            remaining = data.get("remaining_volume", "?")
            executed = data.get("executed_volume", "0")
            await publish_to_telegram({
                "type": "text",
                "chat_id": chat_id,
                "text": (
                    f"⚠️ `{market}` {side_ko} 주문 미체결\n"
                    f"체결: `{executed}` / 잔량: `{remaining}`\n"
                    f"주문 ID: `{order_uuid}`"
                ),
                "parse_mode": "Markdown",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "❌ 주문 취소",
                             "callback_data": f"order_cancel:{order_uuid}:{market}"},
                            {"text": "⏳ 유지",
                             "callback_data": f"order_keep:{order_uuid}"},
                        ],
                    ],
                },
            })

    elif state == "cancel":
        if chat_id:
            await publish_to_telegram({
                "type": "text",
                "chat_id": chat_id,
                "text": f"❌ `{market}` {side_ko} 주문 취소됨\n주문 ID: `{order_uuid}`",
                "parse_mode": "Markdown",
            })


async def handle_cancel_order(payload: dict, settings: Settings) -> None:
    """주문 취소.

    payload:
      - uuid (str): 주문 ID
      - market (str)
      - chat_id (str, optional)
    """
    order_uuid = payload.get("uuid", "")
    market = payload.get("market", "")
    chat_id = payload.get("chat_id", settings.telegram_chat_id)

    if not order_uuid:
        logger.warning("cancel_order: uuid 없음")
        return

    try:
        await asyncio.to_thread(_cancel_sync, settings, order_uuid)
        logger.info("cancel_order: %s 취소 완료 uuid=%s", market, order_uuid[:8])

        if chat_id:
            await publish_to_telegram({
                "type": "text",
                "chat_id": chat_id,
                "text": f"✅ `{market}` 주문 취소 완료\n주문 ID: `{order_uuid}`",
                "parse_mode": "Markdown",
            })
    except Exception:
        logger.exception("cancel_order 실패: %s", order_uuid)
        if chat_id:
            await publish_to_telegram({
                "type": "text",
                "chat_id": chat_id,
                "text": f"❌ 주문 취소 실패\n주문 ID: `{order_uuid}`\n이미 체결되었을 수 있습니다.",
                "parse_mode": "Markdown",
            })

"""텔레그램 인라인 버튼 콜백 핸들러 — 매매 실행/패스."""

from __future__ import annotations

import logging
from typing import Any

from infra.api.active_alert import mark_alert_acted, mark_alert_passed
from infra.api.portfolio import get_user_portfolio
from infra.api.user import get_active_users
from infra.redis_client import get_redis
from infra.redis_queue import push_trade_job
from utils.config import Settings

from telegram.bot import answer_callback, send_message

logger = logging.getLogger(__name__)


async def handle_callback(
    callback_query: dict[str, Any], settings: Settings,
) -> None:
    """콜백 쿼리 처리 — trade_execute/trade_sell/trade_pass."""
    token = settings.telegram_bot_token
    cb_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))

    parts = data.split(":")

    try:
        if data.startswith("trade_execute:"):
            # trade_execute:{market}:BUY:{buy_amount_krw}
            market = parts[1]
            side = parts[2]
            buy_amount = float(parts[3])
            await _handle_trade_execute(token, chat_id, market, side, buy_amount)
            await answer_callback(token, cb_id, "주문 요청 전달")

        elif data.startswith("trade_sell:"):
            # trade_sell:{market}:{price}:{pct}
            market = parts[1]
            price = float(parts[2])
            pct = int(parts[3])
            await _handle_trade_sell(token, chat_id, market, price, pct)
            await answer_callback(token, cb_id, f"매도 {pct}% 요청 전달")

        elif data.startswith("trade_pass:"):
            # trade_pass:{market}
            market = parts[1]
            await _handle_pass(token, chat_id, market)
            await answer_callback(token, cb_id, "패스 처리 완료")

        else:
            await answer_callback(token, cb_id, "알 수 없는 액션")

    except Exception:
        logger.exception("callback 처리 실패: %s", data)
        await answer_callback(token, cb_id, "처리 중 오류 발생")


async def _handle_trade_execute(
    token: str, chat_id: str, market: str, side: str, buy_amount: float,
) -> None:
    """매수 실행 → ct-worker에 place_order Job 전달."""
    if side == "BUY":
        r = get_redis()
        if await r.exists(f"defense:{market}"):
            await send_message(
                token, chat_id,
                f"🛡️ `{market}` 방어 모드 활성 중 — 매수가 차단되었습니다.",
            )
            logger.warning("trade_execute 차단: %s 방어 모드 활성", market)
            return

    if buy_amount < 5000:
        await send_message(token, chat_id, f"⚠️ `{market}` 매수 금액 부족 ({buy_amount:.0f}원)")
        return

    user_id = await _resolve_user_id(chat_id)
    if not user_id:
        await send_message(token, chat_id, "⚠️ 유저 정보를 찾을 수 없습니다.")
        return

    await push_trade_job({
        "type": "place_order",
        "user_id": str(user_id),
        "payload": {
            "market": market,
            "side": "bid",
            "price": str(int(buy_amount)),
            "ord_type": "price",
            "chat_id": chat_id,
        },
    })
    await mark_alert_acted(market)
    await send_message(token, chat_id, f"✅ `{market}` 매수 {buy_amount:,.0f}원 주문 전달")
    logger.info("trade_execute: user=%d %s BUY %d원", user_id, market, buy_amount)


async def _handle_trade_sell(
    token: str, chat_id: str, market: str, price: float, pct: int,
) -> None:
    """매도 실행 → 보유량 조회 후 ct-worker에 place_order Job 전달."""
    user_id = await _resolve_user_id(chat_id)
    if not user_id:
        await send_message(token, chat_id, "⚠️ 유저 정보를 찾을 수 없습니다.")
        return

    coin_currency = market.split("-")[1] if "-" in market else ""
    portfolio = await get_user_portfolio(user_id)
    coin_balance = 0.0
    for item in portfolio:
        if item.get("currency") == coin_currency:
            coin_balance = float(item.get("balance", 0))
            break

    if coin_balance <= 0:
        await send_message(token, chat_id, f"⚠️ `{market}` 보유량이 없습니다.")
        return

    sell_volume = coin_balance * pct / 100
    await push_trade_job({
        "type": "place_order",
        "user_id": str(user_id),
        "payload": {
            "market": market,
            "side": "ask",
            "volume": str(sell_volume),
            "ord_type": "market",
            "chat_id": chat_id,
        },
    })
    await mark_alert_acted(market)
    await send_message(
        token, chat_id,
        f"✅ `{market}` 매도 {pct}% ({sell_volume:.8f}) 주문 전달",
    )
    logger.info("trade_sell: %s %d%% vol=%.8f", market, pct, sell_volume)


async def _resolve_user_id(chat_id: str) -> int | None:
    """chat_id로 유저 ID 조회."""
    users = await get_active_users()
    for u in users:
        if str(u.get("telegram_id")) == str(chat_id):
            return u["id"]
    return None


async def _handle_pass(token: str, chat_id: str, market: str) -> None:
    """패스 처리 → ActiveAlert 상태 변경."""
    await mark_alert_passed(market)
    await send_message(token, chat_id, f"⏭️ `{market}` 시그널을 패스했습니다.")
    logger.info("trade_pass: %s", market)

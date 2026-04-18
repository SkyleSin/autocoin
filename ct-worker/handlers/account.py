"""check_account 핸들러 — 잔고 조회 후 주문 실행."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from handlers.bithumb_api import BithumbAPIError, get_accounts, place_order
from infra.api.trade import get_daily_trade_count
from infra.redis_pubsub import publish_telegram
from infra.redis_queue import push_delayed_job
from utils.config import Settings

logger = logging.getLogger(__name__)

MIN_ORDER_KRW = 5_000  # 빗썸 최소 주문금액


async def handle_check_account(settings: Settings, job_data: dict[str, Any]) -> None:
    """잔고 조회 → pct 적용 → 리스크 체크 → 주문 실행."""
    payload = job_data.get("payload", {})
    action = str(payload.get("action", "")).upper()  # BUY | SELL
    market = str(payload.get("market", ""))
    pct = float(payload.get("pct", 100))
    on_complete = payload.get("on_complete")
    chat_id = str(job_data.get("chat_id", settings.telegram_chat_id))

    if not market or action not in ("BUY", "SELL"):
        logger.warning("check_account: 잘못된 payload — %s", payload)
        return

    try:
        accounts = await asyncio.to_thread(get_accounts, settings)
    except BithumbAPIError:
        logger.exception("잔고 조회 실패: %s", market)
        await publish_telegram(chat_id, f"❌ `{market}` 잔고 조회 실패")
        return

    coin_currency = market.split("-")[1] if "-" in market else ""
    krw_acc = next((a for a in accounts if a.get("currency") == "KRW"), None)
    coin_acc = next((a for a in accounts if a.get("currency") == coin_currency), None)
    krw_balance = float(krw_acc["balance"]) if krw_acc else 0.0
    coin_balance = float(coin_acc["balance"]) if coin_acc else 0.0

    if action == "BUY":
        order_result = await _execute_buy(
            settings, accounts, market, krw_balance, pct, chat_id,
        )
    else:
        order_result = await _execute_sell(
            settings, market, coin_currency, coin_balance, pct, chat_id,
        )

    if order_result and on_complete:
        # 체결 확인 후 on_complete 체이닝
        await push_delayed_job(
            {
                "type": "confirm_order",
                "payload": {
                    "uuid": order_result["uuid"],
                    "market": market,
                    "side": order_result["side"],
                    "on_complete": on_complete,
                    "retry_count": 0,
                },
                "chat_id": chat_id,
            },
            time.time() + 30,
        )
    elif order_result:
        await push_delayed_job(
            {
                "type": "confirm_order",
                "payload": {
                    "uuid": order_result["uuid"],
                    "market": market,
                    "side": order_result["side"],
                    "retry_count": 0,
                },
                "chat_id": chat_id,
            },
            time.time() + 30,
        )


async def _execute_buy(
    settings: Settings,
    accounts: list[dict],
    market: str,
    krw_balance: float,
    pct: float,
    chat_id: str,
) -> dict | None:
    """매수 실행. 잔고 부족 시 insufficient_balance 이벤트 발행."""
    available = krw_balance - settings.risk_min_krw_reserve
    amount_krw = available * (pct / 100)

    if amount_krw < MIN_ORDER_KRW:
        logger.info("잔고 부족: %s (available=%.0f, needed=%.0f)", market, available, amount_krw)
        from infra.redis_pubsub import publish_event
        await publish_event({
            "type": "insufficient_balance",
            "market": market,
            "action": "BUY",
            "needed_krw": amount_krw,
            "available_krw": available,
            "portfolio": _summarize_portfolio(accounts),
        })
        await publish_telegram(
            chat_id,
            f"⚠️ `{market}` 매수 불가 — 잔고 부족\n"
            f"필요: `{amount_krw:,.0f}원` / 가용: `{available:,.0f}원`",
        )
        return None

    # 일일 매매 횟수 체크
    daily_count = await get_daily_trade_count()
    if daily_count >= settings.risk_max_daily_trades:
        await publish_telegram(
            chat_id,
            f"❌ `{market}` 매수 거부 — 일일 한도 초과 "
            f"({daily_count}/{settings.risk_max_daily_trades})",
        )
        return None

    try:
        data = await asyncio.to_thread(
            place_order, settings, market, "bid",
            price=str(int(amount_krw)), ord_type="price",
        )
    except BithumbAPIError:
        logger.exception("매수 주문 실패: %s", market)
        await publish_telegram(chat_id, f"❌ `{market}` 매수 주문 실패")
        return None

    await publish_telegram(
        chat_id,
        f"📝 `{market}` 매수 주문 접수\n"
        f"금액: `{amount_krw:,.0f}원` ({pct:.0f}%)\n"
        f"주문 ID: `{data.get('uuid', '')}`",
    )
    return data


async def _execute_sell(
    settings: Settings,
    market: str,
    coin_currency: str,
    coin_balance: float,
    pct: float,
    chat_id: str,
) -> dict | None:
    """매도 실행."""
    if coin_balance <= 0:
        await publish_telegram(chat_id, f"❌ `{market}` 매도 불가 — 보유 코인 없음")
        return None

    sell_volume = coin_balance * (pct / 100)

    try:
        data = await asyncio.to_thread(
            place_order, settings, market, "ask",
            volume=str(sell_volume), ord_type="market",
        )
    except BithumbAPIError:
        logger.exception("매도 주문 실패: %s", market)
        await publish_telegram(chat_id, f"❌ `{market}` 매도 주문 실패")
        return None

    await publish_telegram(
        chat_id,
        f"📝 `{market}` 매도 주문 접수\n"
        f"수량: `{sell_volume}` {coin_currency} ({pct:.0f}%)\n"
        f"주문 ID: `{data.get('uuid', '')}`",
    )
    return data


def _summarize_portfolio(accounts: list[dict]) -> list[dict]:
    """보유 코인 요약 (insufficient_balance 이벤트용)."""
    return [
        {
            "currency": a["currency"],
            "balance": float(a.get("balance", 0)),
            "avg_buy_price": float(a.get("avg_buy_price", 0)),
        }
        for a in accounts
        if float(a.get("balance", 0)) > 0
    ]

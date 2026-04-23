"""ct-advisor 메인 루프 — 1-step 처리.

advisor_request 수신 → portfolio/risk 조회 → 포지셔닝 → ct-telegram 전달
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from infra.api.portfolio import get_user_portfolio, shadow_execute
from infra.api.user import get_active_users
from infra.redis_client import get_redis
from infra.redis_pubsub import publish_master, publish_to_telegram
from utils.config import Settings

from advisor.position_sizing import (
    calculate_buy_allocation,
    calculate_sell_recommendation,
)

logger = logging.getLogger(__name__)

ADVISOR_REQUEST = "advisor_request"

# risk_settings 기본값
_DEFAULT_RISK = {
    "max_per_trade_pct": 15.0,
    "max_daily_trades": 30,
    "min_krw_reserve": 100_000,
}


async def advisor_loop(settings: Settings) -> None:
    """Redis 구독 루프 — advisor_request 채널."""
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(ADVISOR_REQUEST)
    logger.info("advisor_loop: 시작 (channel: %s)", ADVISOR_REQUEST)

    try:
        while True:
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0,
            )
            if msg is None:
                await asyncio.sleep(0.1)
                continue
            if msg["type"] != "message":
                continue

            try:
                data: dict[str, Any] = json.loads(msg["data"])
                await _handle_signal(data, settings)
            except Exception:
                logger.exception("advisor: 이벤트 처리 실패")

    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(ADVISOR_REQUEST)
        await pubsub.aclose()
        logger.info("advisor_loop: 종료")


async def _handle_signal(data: dict[str, Any], settings: Settings) -> None:
    """시그널 수신 → 포트폴리오/리스크 조회 → 포지셔닝 → 텔레그램."""
    signal_type = data.get("signal_type", "")
    market = data.get("market", "")
    chat_id = data.get("chat_id", "")

    # BUY/SELL 아닌 메시지는 그대로 전달
    if signal_type not in ("BUY", "SELL"):
        await publish_to_telegram(data)
        return

    # 유저 정보 조회 (risk_settings 포함)
    risk = _DEFAULT_RISK.copy()
    user_id = None
    is_shadow = False

    if chat_id:
        users = await get_active_users()
        user = next(
            (u for u in users if str(u.get("telegram_id")) == str(chat_id)),
            None,
        )
        if user:
            user_id = user["id"]
            is_shadow = user.get("is_shadow", False)
            user_risk = user.get("risk_settings") or {}
            risk.update(user_risk)

    # 리스크 체크 — 일일 매매 횟수 (유저별)
    from infra.api.trade import get_daily_trade_count
    daily_count = await get_daily_trade_count(user_id=user_id)
    max_daily = int(risk.get("max_daily_trades", 30))
    if daily_count >= max_daily:
        data["type"] = "text"
        data["text"] = (
            f"⚠️ `{market}` {signal_type} 시그널 차단: "
            f"일일 매매 횟수 초과 ({daily_count}/{max_daily})"
        )
        if not is_shadow:
            await publish_to_telegram(data)
        return

    # 포트폴리오 조회
    krw_balance = 0.0
    coin_balance = 0.0
    coin_currency = market.split("-")[1] if "-" in market else ""

    if user_id:
        portfolio = await get_user_portfolio(user_id)
        for item in portfolio:
            bal = float(item.get("balance", 0))
            if item["currency"] == "KRW":
                krw_balance = bal
            elif item["currency"] == coin_currency:
                coin_balance = bal

    confidence = data.get("confidence", 0.0)
    indicators = data.get("indicators") or {}
    pat_dict = indicators.get("patterns", {})
    pat_list = pat_dict.get("patterns", []) if isinstance(pat_dict, dict) else []

    max_per_trade = float(risk.get("max_per_trade_pct", 15.0))
    min_reserve = float(risk.get("min_krw_reserve", 100_000))

    if signal_type == "BUY":
        available_krw = max(0, krw_balance - min_reserve)

        buy_rec = calculate_buy_allocation(
            patterns=pat_list or None,
            confidence=confidence,
            indicators=indicators,
            max_per_trade_pct=max_per_trade,
        )
        amount_krw = round(available_krw * (buy_rec.allocation_pct / 100), 0)

        data["buy_allocation_pct"] = buy_rec.allocation_pct
        data["buy_amount_krw"] = amount_krw
        data["krw_balance"] = krw_balance
        data["sizing_adjustments"] = buy_rec.adjustments

        if amount_krw < 5000:
            if not is_shadow:
                data["type"] = "text"
                data["text"] = (
                    f"⚠️ `{market}` BUY 시그널 — "
                    f"주문 금액 부족 ({amount_krw:,.0f}원 < 5,000원)"
                )
                await publish_to_telegram(data)
            logger.info("advisor: %s BUY 금액 부족 (%.0f원)", market, amount_krw)
            return

        logger.info(
            "advisor: %s BUY %.1f%% = %d원 (가용: %d원)",
            market, buy_rec.allocation_pct, amount_krw, available_krw,
        )

    elif signal_type == "SELL":
        sell_rec = calculate_sell_recommendation(
            patterns=pat_list or None,
            confidence=confidence,
        )
        close_price = data.get("close_price", 0)

        data["sell_recommended_pct"] = sell_rec.recommended_pct
        data["sell_choices"] = sell_rec.choices
        data["coin_balance"] = coin_balance

        if coin_balance <= 0:
            logger.info("advisor: %s SELL — 보유량 없음, 스킵", market)
            return

        # 먼지 잔량 체크 (₩5,000 미만 전량 매도)
        if close_price > 0:
            krw_value = coin_balance * close_price
            if krw_value <= 5000:
                data["sell_recommended_pct"] = 100
                data["sell_choices"] = [100]
                data["force_full_sell"] = True
                logger.info(
                    "advisor: %s SELL 먼지 잔량 전량 매도 (%.0f원)",
                    market, krw_value,
                )

        logger.info(
            "advisor: %s SELL %d%% (보유: %.8f)",
            market, sell_rec.recommended_pct, coin_balance,
        )

    # shadow 유저: 텔레그램 대신 자동 체결
    if is_shadow and user_id:
        await _shadow_auto_execute(data, user_id, market, signal_type, settings)
        return

    await publish_to_telegram(data)


async def _shadow_auto_execute(
    data: dict[str, Any],
    user_id: int,
    market: str,
    signal_type: str,
    settings: Settings,
) -> None:
    """shadow 유저 자동 체결 — ct-api shadow-execute 호출."""
    close_price = data.get("close_price", 0)
    if close_price <= 0:
        return

    payload: dict[str, Any] = {
        "user_id": user_id,
        "market": market,
        "price": close_price,
    }

    if signal_type == "BUY":
        amount_krw = data.get("buy_amount_krw", 0)
        if amount_krw < 5000:
            return
        payload["side"] = "buy"
        payload["amount_krw"] = amount_krw
    elif signal_type == "SELL":
        sell_pct = data.get("sell_recommended_pct", 60)
        payload["side"] = "sell"
        payload["sell_pct"] = sell_pct
    else:
        return

    try:
        result = await shadow_execute(payload)
        if result.get("executed"):
            coin = market.replace("KRW-", "")
            side = "매수" if signal_type == "BUY" else "매도"
            qty = result.get("qty", 0)
            amt = result.get("amount_krw", 0)
            logger.info(
                "shadow 자동체결: %s %s %.8f개 (%.0f원)",
                market, side, qty, amt,
            )
            master_chat = str(settings.master_telegram_id)
            await publish_master(
                master_chat,
                f"👻 shadow {side}: `{coin}` "
                f"{qty:.4f}개 ({amt:,.0f}원)",
            )
        else:
            logger.info("shadow 체결 스킵: %s %s", market, result.get("reason"))
    except Exception:
        logger.exception("shadow 자동체결 실패: %s", market)

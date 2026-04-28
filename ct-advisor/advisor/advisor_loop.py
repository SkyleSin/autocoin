"""ct-advisor 메인 루프 — 1-step 처리.

advisor_request 수신 → portfolio/risk 조회 → 포지셔닝 → ct-telegram 전달
"""

from __future__ import annotations

import logging
from typing import Any

from infra.api.portfolio import get_user_portfolio, shadow_execute
from infra.api.user import get_active_users
from infra.redis_pubsub import publish_master, publish_telegram, publish_to_telegram
from infra.redis_queue import push_trade_job
from infra.redis_stream import STREAM_ADVISOR, stream_consume
from utils.config import Settings

from advisor.position_sizing import (
    calculate_buy_allocation,
    calculate_sell_recommendation,
)

logger = logging.getLogger(__name__)

_GROUP = "advisor-group"

# risk_settings 기본값
_DEFAULT_RISK = {
    "max_per_trade_pct": 15.0,
    "max_daily_trades": 30,
    "min_krw_reserve": 100_000,
}


async def advisor_loop(settings: Settings) -> None:
    """Redis Streams 소비 루프 — advisor_request."""

    async def handler(data: dict[str, Any]) -> None:
        msg_type = data.get("type", "")
        if msg_type in (
            "safety", "safety_llm_fail",
            "shock_sell", "shock_hold", "shock_llm_fail",
            "defense_sell", "defense_alert",
        ):
            await _handle_defense(data, settings)
        else:
            await _handle_signal(data, settings)

    await stream_consume(STREAM_ADVISOR, _GROUP, "advisor-1", handler)


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


# ---------------------------------------------------------------------------
# 방어 시그널 처리 (safety/shock에서 판단 결과 수신)
# ---------------------------------------------------------------------------


async def _execute_defense_sell(
    user_id: int, market: str, volume: float, reason: str,
) -> None:
    """방어 매도 — Redis 매매 큐로 전송."""
    await push_trade_job({
        "type": "place_order",
        "user_id": str(user_id),
        "payload": {
            "market": market,
            "side": "ask",
            "volume": str(volume),
            "ord_type": "market",
            "defense_reason": reason,
        },
    })
    logger.info("defense sell: user=%d %s vol=%s (%s)", user_id, market, volume, reason)


async def _handle_defense(data: dict[str, Any], settings: Settings) -> None:
    """방어 시그널 처리 — 매도 실행 + 알림 발송만 담당.

    상태 관리(enter/exit/save)는 ct-strategy에서 완료된 상태로 수신.
    """
    msg_type = data["type"]
    chat_id = str(data.get("chat_id", ""))
    market = data.get("market", "")

    # LLM 실패 알림
    if msg_type == "safety_llm_fail":
        breach_pct = data.get("breach_pct", 0)
        if chat_id:
            await publish_telegram(
                chat_id,
                f"⚠️ `{market}` 지지선 이탈 감지 ({breach_pct:.1f}%)\n"
                f"LLM 호출 실패 — 수동 확인 필요",
            )
        return

    if msg_type == "shock_llm_fail":
        shock_level = data.get("shock_level", "")
        signals = data.get("triggered_signals", [])
        if chat_id:
            await publish_telegram(
                chat_id,
                f"⚡ `{market}` 충격 감지 ({shock_level})\n"
                f"신호: {', '.join(signals)}\n"
                f"LLM 호출 실패 — 수동 확인 필요",
            )
        return

    # safety 판단 결과 → 매도 + 알림
    if msg_type == "safety":
        await _handle_safety_result(data, settings)
        return

    # shock HOLD 판단 → 알림만
    if msg_type == "shock_hold":
        if chat_id:
            await publish_telegram(
                chat_id,
                f"⚡ *충격 감지 — HOLD 판단*\n\n"
                f"마켓: `{market}` ({data.get('shock_level', '')})\n"
                f"신호: {', '.join(data.get('triggered_signals', []))}\n"
                f"현재가: `{data.get('current_price', 0):,.0f}`\n\n"
                f"*LLM:* {data.get('llm_reason', '')}\n"
                f"확신도: {data.get('llm_confidence', 0):.0%}",
            )
        return

    # shock 매도 / 손절 매도 → 매도 실행 + 알림
    if msg_type in ("shock_sell", "defense_sell"):
        user_id = data.get("user_id")
        sell_volume = data.get("sell_volume", 0)
        sell_reason = data.get("sell_reason", "")
        alert_text = data.get("alert_text", "")

        if user_id and sell_volume > 0:
            await _execute_defense_sell(user_id, market, sell_volume, sell_reason)

        if chat_id and alert_text:
            await publish_telegram(chat_id, alert_text)
        return

    # 방어모드 해제 등 알림만
    if msg_type == "defense_alert":
        alert_text = data.get("alert_text", "")
        if chat_id and alert_text:
            await publish_telegram(chat_id, alert_text)
        return


async def _handle_safety_result(data: dict[str, Any], settings: Settings) -> None:
    """safety 판단 결과 처리 — 긴급 매도 또는 경고."""
    user_id = data["user_id"]
    chat_id = str(data.get("chat_id", ""))
    market = data["market"]
    balance = data["balance"]
    price = data["current_price"]
    support = data["support_level"]
    breach_pct = data["breach_pct"]
    decision = data["llm_decision"]
    reason = data["llm_reason"]
    confidence = data["llm_confidence"]

    if decision == "SELL":
        await _execute_defense_sell(user_id, market, balance, "긴급 매도 (지지선 이탈)")
        if chat_id:
            await publish_telegram(
                chat_id,
                f"🔴 *긴급 매도 실행*\n\n"
                f"마켓: `{market}`\n"
                f"현재가: `{price:,.0f}`\n"
                f"지지선: `{support:,.0f}` (이탈 {breach_pct:.1f}%)\n"
                f"매도 수량: `{balance}`\n\n"
                f"*LLM 판단:* {reason}\n"
                f"확신도: {confidence:.0%}",
            )
    else:
        if chat_id:
            await publish_telegram(
                chat_id,
                f"⚠️ *지지선 이탈 감지*\n\n"
                f"마켓: `{market}`\n"
                f"현재가: `{price:,.0f}`\n"
                f"지지선: `{support:,.0f}` (이탈 {breach_pct:.1f}%)\n\n"
                f"*LLM 판단: 유지*\n{reason}\n"
                f"확신도: {confidence:.0%}",
            )

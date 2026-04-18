"""ct-advisor 메인 루프 — step 기반 처리.

Step 1: advisor_request 수신 → 상태 저장 → ct-worker에 check_balance 요청
Step 2: advisor_response 수신 (잔고) → 포지셔닝/리스크 → ct-telegram 전달
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from advisor.position_sizing import (
    calculate_buy_allocation,
    calculate_sell_recommendation,
)
from advisor.risk_manager import check_risk
from infra.redis_client import get_redis
from infra.redis_pubsub import publish_to_telegram
from infra.redis_queue import push_trade_job
from utils.config import Settings

logger = logging.getLogger(__name__)

ADVISOR_REQUEST = "advisor_request"
ADVISOR_RESPONSE = "advisor_response"
STATE_PREFIX = "advisor:pending:"
STATE_TTL = 300  # 5분


async def advisor_loop(settings: Settings) -> None:
    """Redis 구독 루프 — advisor_request + advisor_response 채널."""
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(ADVISOR_REQUEST, ADVISOR_RESPONSE)
    logger.info("advisor_loop: 시작 (channels: %s, %s)", ADVISOR_REQUEST, ADVISOR_RESPONSE)

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

            channel = msg.get("channel", b"").decode() if isinstance(msg.get("channel"), bytes) else msg.get("channel", "")

            try:
                data: dict[str, Any] = json.loads(msg["data"])

                if channel == ADVISOR_REQUEST:
                    await _step1_signal_received(data, settings)
                elif channel == ADVISOR_RESPONSE:
                    await _step2_balance_received(data, settings)

            except Exception:
                logger.exception("advisor: 이벤트 처리 실패 (channel=%s)", channel)

    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(ADVISOR_REQUEST, ADVISOR_RESPONSE)
        await pubsub.aclose()
        logger.info("advisor_loop: 종료")


# ---------------------------------------------------------------------------
# Step 1: 시그널 수신 → 상태 저장 → 잔고 조회 요청
# ---------------------------------------------------------------------------

async def _step1_signal_received(data: dict[str, Any], settings: Settings) -> None:
    """시그널 수신 → Redis에 상태 저장 → ct-worker에 check_balance 요청."""
    signal_type = data.get("signal_type", "")
    market = data.get("market", "")

    if signal_type not in ("BUY", "SELL"):
        await publish_to_telegram(data)
        return

    # 리스크 체크 (일일 횟수 등 — 잔고 불필요한 항목)
    risk = await check_risk(settings, side=signal_type, market=market)
    if not risk.allowed:
        logger.info("advisor: 리스크 차단 %s %s — %s", market, signal_type, risk.reason)
        data["type"] = "text"
        data["text"] = f"⚠️ `{market}` {signal_type} 시그널 차단: {risk.reason}"
        await publish_to_telegram(data)
        return

    # 상태 저장 (Redis)
    request_id = str(uuid.uuid4())
    r = get_redis()
    await r.set(
        f"{STATE_PREFIX}{request_id}",
        json.dumps(data, ensure_ascii=False),
        ex=STATE_TTL,
    )

    # ct-worker에 잔고 조회 요청
    await push_trade_job({
        "type": "check_balance",
        "request_id": request_id,
        "response_channel": ADVISOR_RESPONSE,
        "user_id": data.get("user_id", "master"),
        "payload": {"market": market},
    })

    logger.info("advisor step1: %s %s — 잔고 조회 요청 (id=%s)", market, signal_type, request_id[:8])


# ---------------------------------------------------------------------------
# Step 2: 잔고 응답 수신 → 포지셔닝 → ct-telegram 전달
# ---------------------------------------------------------------------------

async def _step2_balance_received(data: dict[str, Any], settings: Settings) -> None:
    """ct-worker 잔고 응답 수신 → 포지셔닝 계산 → ct-telegram 전달."""
    request_id = data.get("request_id")
    if not request_id:
        logger.warning("advisor step2: request_id 없음")
        return

    # 저장된 시그널 상태 복원
    r = get_redis()
    state_key = f"{STATE_PREFIX}{request_id}"
    raw = await r.get(state_key)
    if not raw:
        logger.warning("advisor step2: 상태 만료 또는 없음 (id=%s)", request_id[:8])
        return
    await r.delete(state_key)

    signal_data: dict[str, Any] = json.loads(raw)
    signal_type = signal_data.get("signal_type", "")
    market = signal_data.get("market", "")
    confidence = signal_data.get("confidence", 0.0)
    indicators = signal_data.get("indicators") or {}

    # 잔고 파싱
    result = data.get("result", {})
    accounts = result.get("accounts", [])

    krw_balance = 0.0
    coin_balance = 0.0
    coin_currency = market.split("-")[1] if "-" in market else ""

    for acc in accounts:
        if acc["currency"] == "KRW":
            krw_balance = acc["balance"]
        elif acc["currency"] == coin_currency:
            coin_balance = acc["balance"]

    # 패턴 추출
    pat_dict = indicators.get("patterns", {})
    pat_list = pat_dict.get("patterns", []) if isinstance(pat_dict, dict) else []

    # 포지션 사이징
    if signal_type == "BUY":
        buy_rec = calculate_buy_allocation(
            patterns=pat_list or None,
            confidence=confidence,
            indicators=indicators,
            max_per_trade_pct=settings.risk_max_per_trade_pct,
        )
        amount_krw = round(krw_balance * (buy_rec.allocation_pct / 100), 0)

        signal_data["buy_allocation_pct"] = buy_rec.allocation_pct
        signal_data["buy_amount_krw"] = amount_krw
        signal_data["krw_balance"] = krw_balance
        signal_data["sizing_adjustments"] = buy_rec.adjustments

        # 최소 주문금액 체크
        if amount_krw < 5000:
            signal_data["type"] = "text"
            signal_data["text"] = (
                f"⚠️ `{market}` BUY 시그널 — "
                f"주문 금액 부족 ({amount_krw:,.0f}원 < 5,000원)"
            )
            await publish_to_telegram(signal_data)
            logger.info("advisor step2: %s BUY 금액 부족 (%.0f원)", market, amount_krw)
            return

        logger.info(
            "advisor step2: %s BUY %.1f%% = %,.0f원 (잔고: %,.0f원)",
            market, buy_rec.allocation_pct, amount_krw, krw_balance,
        )

    elif signal_type == "SELL":
        sell_rec = calculate_sell_recommendation(
            patterns=pat_list or None,
            confidence=confidence,
        )
        close_price = signal_data.get("close_price", 0)

        signal_data["sell_recommended_pct"] = sell_rec.recommended_pct
        signal_data["sell_choices"] = sell_rec.choices
        signal_data["coin_balance"] = coin_balance

        # 보유량 체크
        if coin_balance <= 0:
            logger.info("advisor step2: %s SELL — 보유량 없음, 스킵", market)
            return

        # 먼지 잔량 체크 (₩5,000 미만 전량 매도)
        if close_price > 0:
            krw_value = coin_balance * close_price
            if krw_value <= 5000:
                signal_data["sell_recommended_pct"] = 100
                signal_data["sell_choices"] = [100]
                signal_data["force_full_sell"] = True
                logger.info(
                    "advisor step2: %s SELL 먼지 잔량 전량 매도 (%.0f원)",
                    market, krw_value,
                )

        logger.info(
            "advisor step2: %s SELL %d%% (보유: %.8f)",
            market, sell_rec.recommended_pct, coin_balance,
        )

    # ct-telegram으로 전달
    await publish_to_telegram(signal_data)

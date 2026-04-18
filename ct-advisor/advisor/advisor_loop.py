"""ct-advisor 메인 루프 — Redis advisor_request 구독 → 리스크/포지셔닝 → ct-telegram 전달."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from advisor.position_sizing import (
    calculate_buy_allocation,
    calculate_sell_recommendation,
)
from advisor.risk_manager import check_risk
from infra.redis_client import get_redis
from infra.redis_pubsub import publish_to_telegram
from utils.config import Settings

logger = logging.getLogger(__name__)


async def advisor_loop(settings: Settings) -> None:
    """Redis advisor_request 채널 구독 → 처리 → send_message로 전달."""
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("advisor_request")
    logger.info("advisor_loop: advisor_request 채널 구독 시작")

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
                await _process_signal(data, settings)
            except Exception:
                logger.exception("advisor: 이벤트 처리 실패")

    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe("advisor_request")
        await pubsub.aclose()
        logger.info("advisor_loop: 종료")


async def _process_signal(data: dict[str, Any], settings: Settings) -> None:
    """시그널 데이터에 포지셔닝/리스크 정보를 추가 후 ct-telegram으로 전달."""
    signal_type = data.get("signal_type", "")
    market = data.get("market", "")
    confidence = data.get("confidence", 0.0)
    indicators = data.get("indicators") or {}

    if signal_type not in ("BUY", "SELL"):
        # HOLD 등은 그대로 전달
        await publish_to_telegram(data)
        return

    # 리스크 체크
    risk = await check_risk(settings, side=signal_type, market=market)
    if not risk.allowed:
        logger.info("advisor: 리스크 차단 %s %s — %s", market, signal_type, risk.reason)
        data["type"] = "text"
        data["text"] = f"⚠️ `{market}` {signal_type} 시그널 차단: {risk.reason}"
        await publish_to_telegram(data)
        return

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
        data["buy_allocation_pct"] = buy_rec.allocation_pct
        data["sizing_adjustments"] = buy_rec.adjustments
        logger.info(
            "advisor: %s BUY 추천 %.1f%% (%s)",
            market, buy_rec.allocation_pct, buy_rec.pattern_name,
        )

    elif signal_type == "SELL":
        sell_rec = calculate_sell_recommendation(
            patterns=pat_list or None,
            confidence=confidence,
        )
        data["sell_recommended_pct"] = sell_rec.recommended_pct
        data["sell_choices"] = sell_rec.choices
        logger.info(
            "advisor: %s SELL 추천 %d%% (%s)",
            market, sell_rec.recommended_pct, sell_rec.pattern_name,
        )

    # ct-telegram으로 전달
    await publish_to_telegram(data)

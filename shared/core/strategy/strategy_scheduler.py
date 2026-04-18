"""전략 스케줄러 — Redis Pub/Sub 이벤트 구동.

compute_indicators 완료 시 Redis 'analysis_ready' 채널로 발행된 이벤트를 받아
전략을 실행하고, BUY/SELL이면 직접 텔레그램 알림을 발송한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from core.strategy.base import get_strategy
from core.strategy.signal_notifier import notify_signal
from infra.api.signal import close_opposite_signals, create_signal
from infra.api.user import get_active_users, get_user_full
from infra.redis_client import get_redis
from services.user_service import get_user_settings_from_dict
from utils.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyEvent:
    """전략 실행 이벤트 — compute_indicators 완료 후 발행."""

    market: str
    timeframe: str


async def strategy_scheduler(settings: Settings) -> None:
    """전략 스케줄러 메인 루프. Redis Pub/Sub에서 이벤트 수신."""
    logger.info("strategy_scheduler: started")

    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe("analysis_ready")

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
                data = json.loads(msg["data"])
                event = StrategyEvent(
                    market=data["market"], timeframe=data["timeframe"],
                )
                await _handle_event(event, settings)
            except Exception:
                logger.exception(
                    "strategy_scheduler: error processing message %s",
                    msg.get("data"),
                )
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe("analysis_ready")
        await pubsub.aclose()
        logger.info("strategy_scheduler: stopped")


async def _handle_event(event: StrategyEvent, settings: Settings) -> None:
    """유저 루프: 각 활성 유저에 대해 전략 실행."""
    users = await get_active_users()

    for user_data in users:
        watchlist = user_data.get("coin_watchlist")
        if watchlist and event.market not in watchlist:
            continue
        try:
            full = await get_user_full(user_data["id"])
            if not full:
                continue
            user_settings = get_user_settings_from_dict(full, settings)
            await _evaluate_for_user(event, user_settings, full)
        except Exception:
            logger.exception(
                "유저 %s 전략 평가 실패", user_data.get("username"),
            )


async def _evaluate_for_user(
    event: StrategyEvent, settings: Settings, user_data: dict,
) -> None:
    """단일 유저 전략 실행 -> Signal API 저장 -> 알림 발송."""
    strategy = get_strategy()
    result = await strategy.evaluate(event.market, settings)
    meta = result.metadata or {}

    if meta.get("status") in ("insufficient_data", "compute_failed"):
        return

    close_price = meta.get("close_price", 0.0)
    combined_indicators = meta.get("indicators", {})
    strategy_timeframe = meta.get("timeframe", event.timeframe)

    signal_data = {
        "market": event.market,
        "timeframe": strategy_timeframe,
        "signal_type": result.signal_type,
        "confidence": result.confidence,
        "indicators": combined_indicators,
        "source": "technical",
        "user_id": user_data["id"],
    }
    if result.signal_type in ("BUY", "SELL"):
        signal_data["entry_price"] = close_price
        signal_data["mfe"] = close_price
        signal_data["mae"] = close_price

    await create_signal(signal_data)

    if result.signal_type in ("BUY", "SELL") and close_price > 0:
        resp = await close_opposite_signals(
            event.market, result.signal_type, close_price,
        )
        closed = resp.get("closed", 0)
        if closed:
            logger.info(
                "strategy: closed %d opposite signals for %s",
                closed, event.market,
            )

    logger.info(
        "strategy[%s]: %s %s -> %s (conf=%.2f)",
        user_data.get("username"), event.market, strategy_timeframe,
        result.signal_type, result.confidence,
    )

    if result.signal_type == "BUY":
        from core.safety.defense_mode import is_defense_active

        if await is_defense_active(event.market):
            logger.info("strategy: BUY 차단 -- 방어 모드: %s", event.market)
            return

    if result.signal_type in ("BUY", "SELL"):
        await notify_signal(
            settings,
            market=event.market,
            timeframe=strategy_timeframe,
            signal_type=result.signal_type,
            confidence=result.confidence,
            reasons=result.reasons,
            indicators=combined_indicators,
            close_price=close_price,
            user_data=user_data,
        )

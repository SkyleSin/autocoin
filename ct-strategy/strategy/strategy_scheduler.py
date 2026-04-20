"""전략 스케줄러 — Redis Pub/Sub(analysis_ready) 이벤트 구동."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from infra.api.signal import close_opposite_signals, create_signal
from infra.api.user import get_active_users
from infra.redis_client import get_redis
from utils.config import Settings

from strategy.base import get_strategy
from strategy.signal_notifier import notify_signal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyEvent:
    market: str
    timeframe: str


async def strategy_scheduler(settings: Settings) -> None:
    """전략 스케줄러 메인 루프 — Redis Pub/Sub에서 analysis_ready 이벤트 수신."""
    logger.info("strategy_scheduler: 시작")

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
                tf = data.get("timeframe", "15m")
                if tf != "15m":
                    continue
                event = StrategyEvent(market=data["market"], timeframe=tf)
                await _handle_event(event, settings)
            except Exception:
                logger.exception("strategy_scheduler: 이벤트 처리 실패 %s", msg.get("data"))

    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe("analysis_ready")
        await pubsub.aclose()
        logger.info("strategy_scheduler: 종료")


async def _handle_event(event: StrategyEvent, settings: Settings) -> None:
    """활성 유저별 전략 실행."""
    users = await get_active_users()
    for user_data in users:
        watchlist = user_data.get("coin_watchlist")
        if not watchlist or event.market not in watchlist:
            continue
        try:
            await _evaluate_for_user(event, settings, user_data)
        except Exception:
            logger.exception("유저 %s 전략 평가 실패", user_data.get("username"))


async def _evaluate_for_user(
    event: StrategyEvent, settings: Settings, user_data: dict,
) -> None:
    """단일 유저 전략 실행 → Signal 저장 → BUY/SELL 시 알림."""
    # 전략 로드 (strategy_topdown_mtf.py import 시 자동 등록)
    import strategy.strategy_topdown_mtf  # noqa: F401

    strategy = get_strategy()
    result = await strategy.evaluate(event.market, settings)
    meta = result.metadata or {}

    if meta.get("status") in ("insufficient_data", "compute_failed"):
        return

    close_price = 0.0
    combined_indicators: dict = {}
    layers = meta.get("indicators", {})
    for tf_key in ("layer_15m", "layer_1h", "layer_4h"):
        layer = layers.get(tf_key, {})
        if layer.get("close_price"):
            close_price = float(layer["close_price"])
            combined_indicators = dict(layer.get("indicators", {}))
            break

    # SR 데이터 병합 (15m → 1h → 4h 우선순위로 탐색)
    for tf_key in ("layer_15m", "layer_1h", "layer_4h"):
        sr_data = layers.get(tf_key, {}).get("support_resistance")
        if sr_data:
            combined_indicators["support_resistance"] = sr_data
            break

    # 상위 TF 추세 정보 병합
    layer_4h = layers.get("layer_4h", {})
    if layer_4h.get("indicators"):
        combined_indicators["higher_trend"] = {
            "direction": layers.get("scoring", {}).get("direction", "-"),
            "macd_histogram_4h": layer_4h["indicators"].get("macd_histogram"),
        }

    # scoring 정보 병합
    if layers.get("scoring"):
        combined_indicators["scoring"] = layers["scoring"]

    strategy_timeframe = event.timeframe

    signal_data = {
        "market": event.market,
        "timeframe": strategy_timeframe,
        "signal_type": result.signal_type,
        "confidence": result.confidence,
        "indicators": combined_indicators,
        "source": "technical",
        "user_id": user_data["id"],
    }
    if result.signal_type in ("BUY", "SELL") and close_price > 0:
        signal_data["entry_price"] = close_price
        signal_data["mfe"] = close_price
        signal_data["mae"] = close_price

    await create_signal(signal_data)

    if result.signal_type in ("BUY", "SELL") and close_price > 0:
        try:
            resp = await close_opposite_signals(
                event.market, result.signal_type, close_price,
            )
            closed = resp.get("closed", 0)
            if closed:
                logger.info("반대 시그널 종료: %s %d건", event.market, closed)
        except Exception:
            logger.warning("close_opposite_signals 실패: %s", event.market, exc_info=True)

    logger.info(
        "strategy[%s]: %s %s -> %s (conf=%.2f)",
        user_data.get("username"), event.market, strategy_timeframe,
        result.signal_type, result.confidence,
    )

    if result.signal_type == "BUY" and await _is_defense_active(event.market):
        logger.info("BUY 차단 — 방어 모드: %s", event.market)
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


async def _is_defense_active(market: str) -> bool:
    """방어 모드 확인 — Redis 직접 조회."""
    r = get_redis()
    return bool(await r.exists(f"defense:{market}"))

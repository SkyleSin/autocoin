"""전략 스케줄러 — analysis_loop에서 직접 호출."""

from __future__ import annotations

import logging

from infra.api.signal import close_opposite_signals, create_signal
from infra.api.user import get_active_users
from infra.redis_client import get_redis
from utils.config import Settings

from strategy.base import get_strategy
from strategy.signal_notifier import notify_signal

logger = logging.getLogger(__name__)


_SIGNAL_COOLDOWN_SEC = 14400  # 동일 종목·방향 시그널 DB 저장 쿨다운 (4시간)


async def handle_analysis_complete(
    market: str, timeframe: str, settings: Settings,
) -> None:
    """분석 완료 후 호출 — 활성 유저별 전략 실행."""
    users = await get_active_users()
    for user_data in users:
        watchlist = user_data.get("coin_watchlist")
        if not watchlist or market not in watchlist:
            continue
        try:
            await _evaluate_for_user(market, timeframe, settings, user_data)
        except Exception:
            logger.exception("유저 %s 전략 평가 실패", user_data.get("username"))


async def _evaluate_for_user(
    market: str, timeframe: str, settings: Settings, user_data: dict,
) -> None:
    """단일 유저 전략 실행 → Signal 저장 → BUY/SELL 시 알림."""
    # 전략 로드 (strategy_topdown_mtf.py import 시 자동 등록)
    import strategy.strategy_topdown_mtf  # noqa: F401

    strategy = get_strategy()
    result = await strategy.evaluate(market, settings)
    meta = result.metadata or {}

    if meta.get("status") in ("insufficient_data", "compute_failed"):
        return

    # 동일 종목·방향 시그널 쿨다운 — DB 오염 및 통계 왜곡 방지
    if result.signal_type in ("BUY", "SELL"):
        r = get_redis()
        uid = user_data["id"]
        cooldown_key = f"sig_cooldown:{uid}:{market}:{result.signal_type}"
        if await r.exists(cooldown_key):
            logger.debug(
                "시그널 쿨다운 스킵: %s %s %s",
                market, result.signal_type, user_data.get("username"),
            )
            return
        await r.setex(cooldown_key, _SIGNAL_COOLDOWN_SEC, "1")
        # 반대 방향 쿨다운 제거 (방향 전환 시 즉시 발행 허용)
        opposite = "SELL" if result.signal_type == "BUY" else "BUY"
        await r.delete(f"sig_cooldown:{uid}:{market}:{opposite}")

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

    signal_data = {
        "market": market,
        "timeframe": timeframe,
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
                market, result.signal_type, close_price,
            )
            closed = resp.get("closed", 0)
            if closed:
                logger.info("반대 시그널 종료: %s %d건", market, closed)
        except Exception:
            logger.warning("close_opposite_signals 실패: %s", market, exc_info=True)

    logger.info(
        "strategy[%s]: %s %s -> %s (conf=%.2f)",
        user_data.get("username"), market, timeframe,
        result.signal_type, result.confidence,
    )

    if result.signal_type == "BUY" and await _is_defense_active(market):
        logger.info("BUY 차단 — 방어 모드: %s", market)
        return

    if result.signal_type in ("BUY", "SELL"):
        await notify_signal(
            settings,
            market=market,
            timeframe=timeframe,
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

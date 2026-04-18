"""5분봉 트리거 전략 — 기존 signal_generator 기반.

베이지안 적중률 결합 + S/R 부스트 + MTF 필터.

파이프라인 (indicator_snapshots API에서 지표 조회):
  1) 메인TF 지표 -> 베이지안 시그널
  2) 15m -> 반대 방향 충돌 필터
  3) 1h  -> 동의 부스트(x1.1) / 반대 감쇠(x0.85)
  4) 4h/1h 추세 -> 하락 중 BUY -> HOLD
  5) BTC 일봉 -> bear_market 플래그
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from infra.api.candle import get_candles
from infra.api.indicator_snapshot import get_latest_snapshot

from .base import BaseStrategy, StrategyResult, register_strategy
from .snapshot_helpers import rebuild_snap, rebuild_sr

if TYPE_CHECKING:
    from utils.config import Settings

logger = logging.getLogger(__name__)


async def _get_latest_snap(
    market: str, timeframe: str,
) -> dict[str, Any] | None:
    """indicator_snapshots API에서 최신 지표 조회."""
    return await get_latest_snapshot(market, timeframe)


async def _check_bear_market() -> bool:
    """BTC 일봉 종가 < SMA50이면 하락장."""
    import pandas as pd

    data = await _get_latest_snap("KRW-BTC", "1d")
    if data is not None:
        sma_50 = data.get("sma_50")
        close = data.get("close_price")
        if sma_50 is not None and close is not None:
            is_bear = close < sma_50
            if is_bear:
                logger.info("bear_market: BTC %.0f < SMA50 %.0f", close, sma_50)
            return is_bear
        return False

    rows = await get_candles("KRW-BTC", "1d", limit=60, order="asc")
    if len(rows) < 50:
        return False
    closes = pd.Series([float(r["close"]) for r in rows])
    sma50 = closes.rolling(50).mean()
    if pd.isna(sma50.iloc[-1]):
        return False
    return float(closes.iloc[-1]) < float(sma50.iloc[-1])


async def _evaluate_tf(
    market: str, timeframe: str,
) -> tuple[Any, dict[str, Any] | None]:
    """특정 TF의 indicator_snapshots -> 시그널 평가."""
    from core.analysis.signal_generator import evaluate

    data = await _get_latest_snap(market, timeframe)
    if data is None:
        return None, None
    snap = rebuild_snap(data)
    if snap is None:
        return None, None
    close_price = data.get("close_price", 0.0)
    sr = rebuild_sr(data.get("support_resistance"))
    signal = evaluate(snap, close_price, timeframe=timeframe, sr_analysis=sr)
    detail = {
        "timeframe": timeframe,
        "signal_type": signal.signal_type,
        "confidence": signal.confidence,
        "reasons": signal.reasons,
        "indicators": snap.to_dict(),
    }
    return signal, detail


@register_strategy
class Strategy5mTrigger(BaseStrategy):
    name = "5m_trigger"
    description = "5분봉 베이지안 시그널 + S/R 부스트 + MTF 필터"

    def required_timeframes(self) -> list[str]:
        return ["5m", "15m", "1h", "4h"]

    async def evaluate(self, market: str, settings: Settings) -> StrategyResult:
        """indicator_snapshots API에서 지표를 읽어 시그널 판단."""
        from core.analysis.signal_generator import evaluate
        from core.strategy.strategy_5m_mtf import apply_mtf_filter

        timeframe = "5m"
        main_data = await _get_latest_snap(market, timeframe)
        if main_data is None:
            return StrategyResult("HOLD", 0.0, ["메인TF 지표 없음"],
                                  {"status": "insufficient_data"})

        snap = rebuild_snap(main_data)
        if snap is None:
            return StrategyResult("HOLD", 0.0, ["지표 복원 실패"],
                                  {"status": "compute_failed"})

        close_price = main_data.get("close_price", 0.0)
        sr = rebuild_sr(main_data.get("support_resistance"))
        sig = evaluate(snap, close_price, timeframe=timeframe, sr_analysis=sr)
        combined: dict[str, Any] = dict(main_data)

        ref_tf_str = settings.candle_ref_timeframes
        ref_tfs = [tf.strip() for tf in ref_tf_str.split(",") if tf.strip()]

        if sig.signal_type in ("BUY", "SELL") and ref_tfs:
            sig, combined = await apply_mtf_filter(sig, combined, market, ref_tfs)

        if sig.signal_type in ("BUY", "SELL"):
            combined["bear_market"] = await _check_bear_market()

        return StrategyResult(
            signal_type=sig.signal_type, confidence=sig.confidence,
            reasons=sig.reasons,
            metadata={
                "status": "ok", "timeframe": timeframe,
                "candle_count": main_data.get("candle_count", 0),
                "close_price": close_price, "indicators": combined,
            },
        )

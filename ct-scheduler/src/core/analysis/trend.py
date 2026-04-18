"""상위 타임프레임 추세 판단 — 모멘텀 + 가격 위치 기반.

SMA 20/50 교차는 백테스트에서 무의미(1~2%p 차이)했으므로,
ROC(변화율) + 가격 위치 퍼센타일로 대체.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from infra.api.candle import get_candles

logger = logging.getLogger(__name__)

_LABEL_TO_UNIT = {
    "1m": 1, "3m": 3, "5m": 5, "10m": 10,
    "15m": 15, "30m": 30, "1h": 60, "4h": 240,
}


@dataclass(frozen=True)
class TrendResult:
    """상위 타임프레임 추세 판단 결과."""

    direction: str  # "UP", "DOWN", "NEUTRAL"
    timeframe: str
    sma_20: float | None
    sma_50: float | None
    description: str


async def get_higher_trend(
    market: str,
    ref_timeframes: list[str],
    *,
    candle_limit: int = 60,
) -> TrendResult:
    """참조 타임프레임들의 추세를 종합 판단."""
    sorted_tfs = sorted(
        ref_timeframes,
        key=lambda tf: _LABEL_TO_UNIT.get(tf, 0),
        reverse=True,
    )

    for tf in sorted_tfs:
        result = await _analyze_single_tf(market, tf, candle_limit)
        if result is not None:
            return result

    return TrendResult(
        direction="NEUTRAL",
        timeframe="",
        sma_20=None,
        sma_50=None,
        description="참조 타임프레임 데이터 부족",
    )


async def _analyze_single_tf(
    market: str,
    timeframe: str,
    candle_limit: int,
) -> TrendResult | None:
    """단일 타임프레임의 추세 판단."""
    rows = await get_candles(market, timeframe, limit=candle_limit, order="asc")
    if len(rows) < 50:
        return None

    closes = pd.Series([float(c["close"]) for c in rows])
    highs = pd.Series([float(c["high"]) for c in rows])
    lows = pd.Series([float(c["low"]) for c in rows])

    direction, desc, sma20, sma50 = _classify_trend(
        timeframe, closes, highs, lows,
    )
    logger.info("higher_trend: %s %s -> %s", market, timeframe, direction)

    return TrendResult(
        direction=direction,
        timeframe=timeframe,
        sma_20=sma20,
        sma_50=sma50,
        description=desc,
    )


def _classify_trend(
    timeframe: str,
    closes: pd.Series,
    highs: pd.Series,
    lows: pd.Series,
) -> tuple[str, str, float | None, float | None]:
    """모멘텀(ROC) + 가격 위치 퍼센타일로 추세 판단."""
    recent = closes.iloc[-50:]
    high_50 = float(highs.iloc[-50:].max())
    low_50 = float(lows.iloc[-50:].min())
    price_range = high_50 - low_50
    if price_range == 0:
        position = 50.0
    else:
        position = (float(closes.iloc[-1]) - low_50) / price_range * 100

    if len(closes) >= 11 and float(closes.iloc[-11]) > 0:
        roc = (float(closes.iloc[-1]) / float(closes.iloc[-11]) - 1) * 100
    else:
        roc = 0.0

    sma20 = float(recent.rolling(20).mean().iloc[-1]) if len(recent) >= 20 else None
    sma50 = (
        float(closes.iloc[-50:].rolling(50).mean().iloc[-1])
        if len(closes) >= 50
        else None
    )

    if position > 70 and roc > 0.5:
        desc = f"{timeframe} 상승 추세 (위치 {position:.0f}%, ROC {roc:+.2f}%)"
        return "UP", desc, sma20, sma50

    if position < 30 and roc < -0.5:
        desc = f"{timeframe} 하락 추세 (위치 {position:.0f}%, ROC {roc:+.2f}%)"
        return "DOWN", desc, sma20, sma50

    desc = f"{timeframe} 추세 불명확 (위치 {position:.0f}%, ROC {roc:+.2f}%)"
    return "NEUTRAL", desc, sma20, sma50

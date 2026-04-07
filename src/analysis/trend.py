"""상위 타임프레임 추세 판단 — 멀티 타임프레임 분석 필터."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.indicators import sma
from models.candle import Candle

logger = logging.getLogger(__name__)

# 타임프레임 라벨 → 분봉 단위 매핑
_LABEL_TO_UNIT = {
    "1m": 1, "3m": 3, "5m": 5, "10m": 10,
    "15m": 15, "30m": 30, "1h": 60, "4h": 240,
}


@dataclass(frozen=True)
class TrendResult:
    """상위 타임프레임 추세 판단 결과."""

    direction: str  # "UP", "DOWN", "NEUTRAL"
    timeframe: str  # 판단에 사용한 타임프레임 (예: "4h")
    sma_20: float | None
    sma_50: float | None
    description: str  # 사람이 읽을 수 있는 설명


async def get_higher_trend(
    session: AsyncSession,
    market: str,
    ref_timeframes: list[str],
    *,
    candle_limit: int = 60,
) -> TrendResult:
    """참조 타임프레임들의 추세를 종합 판단.

    가장 큰 타임프레임부터 확인하여, 충분한 데이터가 있는 첫 번째 TF로 판단.
    """
    sorted_tfs = sorted(
        ref_timeframes,
        key=lambda tf: _LABEL_TO_UNIT.get(tf, 0),
        reverse=True,
    )

    for tf in sorted_tfs:
        result = await _analyze_single_tf(session, market, tf, candle_limit)
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
    session: AsyncSession,
    market: str,
    timeframe: str,
    candle_limit: int,
) -> TrendResult | None:
    """단일 타임프레임의 추세 판단."""
    rows = await session.execute(
        select(Candle)
        .where(Candle.market == market, Candle.timeframe == timeframe)
        .order_by(Candle.open_at.desc())
        .limit(candle_limit)
    )
    candles = list(reversed(rows.scalars().all()))

    if len(candles) < 50:
        return None

    closes = pd.Series([float(c.close) for c in candles])
    sma_20 = sma(closes, 20)
    sma_50 = sma(closes, 50)

    last_sma_20 = float(sma_20.iloc[-1])
    last_sma_50 = float(sma_50.iloc[-1])

    # SMA 20 기울기 (최근 3봉 평균 변화)
    recent_sma20 = sma_20.iloc[-3:]
    slope = (
        (float(recent_sma20.iloc[-1]) - float(recent_sma20.iloc[0]))
        / float(recent_sma20.iloc[0])
        * 100
    )

    direction, desc = _classify_trend(
        timeframe, last_sma_20, last_sma_50, slope,
    )
    logger.info("higher_trend: %s %s → %s", market, timeframe, direction)

    return TrendResult(
        direction=direction,
        timeframe=timeframe,
        sma_20=round(last_sma_20, 4),
        sma_50=round(last_sma_50, 4),
        description=desc,
    )


def _classify_trend(
    timeframe: str, sma20: float, sma50: float, slope: float,
) -> tuple[str, str]:
    """SMA 교차 + 기울기로 추세 방향 분류."""
    if sma20 > sma50 and slope > 0:
        return "UP", (
            f"{timeframe} 상승 추세 "
            f"(SMA20 {sma20:,.0f} > SMA50 {sma50:,.0f}, 기울기 +{slope:.2f}%)"
        )
    if sma20 < sma50 and slope < 0:
        return "DOWN", (
            f"{timeframe} 하락 추세 "
            f"(SMA20 {sma20:,.0f} < SMA50 {sma50:,.0f}, 기울기 {slope:.2f}%)"
        )
    return "NEUTRAL", (
        f"{timeframe} 추세 불명확 "
        f"(SMA20 {sma20:,.0f}, SMA50 {sma50:,.0f}, 기울기 {slope:.2f}%)"
    )

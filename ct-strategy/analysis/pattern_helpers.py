"""패턴 인식 헬퍼 (추세선 피팅, 기울기, 선행추세, 거리, 거래량)."""

from __future__ import annotations

import pandas as pd

from .pattern_utils import Trendline


def order_for_timeframe(timeframe: str) -> int:
    """타임프레임별 피크/밸리 탐색 order."""
    return {
        "1m": 15, "3m": 12, "5m": 10, "15m": 10,
        "30m": 8, "1h": 5, "4h": 5, "1d": 5,
    }.get(timeframe, 10)


def min_swing_pct(highs: pd.Series, lows: pd.Series) -> float:
    """고저 변동폭(%)."""
    highest, lowest = float(highs.max()), float(lows.min())
    if lowest == 0:
        return 0.0
    return (highest - lowest) / lowest * 100


def fit_trendline(indices: list[int], prices: list[float]) -> Trendline | None:
    """최소자승법으로 추세선 피팅. 최소 2점 필요."""
    n = len(indices)
    if n < 2:
        return None
    x_mean = sum(indices) / n
    y_mean = sum(prices) / n
    numer = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(indices, prices, strict=True)
    )
    denom = sum((x - x_mean) ** 2 for x in indices)
    if denom == 0:
        return None
    slope = numer / denom
    intercept = y_mean - slope * x_mean
    return Trendline(
        slope=slope, intercept=intercept,
        start_idx=indices[0], end_idx=indices[-1],
    )


def slope_pct(line: Trendline, avg_price: float) -> float:
    """추세선 기울기를 봉당 가격변동률(%)로 환산."""
    if avg_price == 0:
        return 0.0
    return line.slope / avg_price * 100


def has_prior_uptrend(
    closes: pd.Series, before_idx: int, lookback: int = 20, min_pct: float = 1.5,
) -> bool:
    """before_idx 이전 구간에서 상승추세가 있었는지."""
    start = max(0, before_idx - lookback)
    if start >= before_idx:
        return False
    low = float(closes.iloc[start:before_idx + 1].min())
    high = float(closes.iloc[before_idx])
    if low == 0:
        return False
    return (high - low) / low * 100 >= min_pct


def has_prior_downtrend(
    closes: pd.Series, before_idx: int, lookback: int = 20, min_pct: float = 1.5,
) -> bool:
    """before_idx 이전 구간에서 하락추세가 있었는지."""
    start = max(0, before_idx - lookback)
    if start >= before_idx:
        return False
    high = float(closes.iloc[start:before_idx + 1].max())
    low = float(closes.iloc[before_idx])
    if high == 0:
        return False
    return (high - low) / high * 100 >= min_pct


def check_min_max_distance(
    idx1: int, idx2: int, order: int, data_len: int, max_ratio: float = 0.5,
) -> bool:
    """두 피크/밸리 간 최소(order*2)/최대(data_len*ratio) 거리 검증."""
    dist = abs(idx2 - idx1)
    return dist >= order * 2 and dist <= data_len * max_ratio


def check_volume(
    volumes: pd.Series | None, idx: int, lookback: int = 20,
) -> bool | None:
    """돌파/이탈 시점의 거래량이 평균 대비 1.5배 이상인지."""
    if volumes is None or len(volumes) <= lookback:
        return None
    avg_vol = float(volumes.iloc[-lookback:].mean())
    if avg_vol <= 0:
        return None
    return float(volumes.iloc[idx]) >= avg_vol * 1.5

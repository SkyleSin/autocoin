"""ATR(Average True Range) 계산 유틸리티."""

from __future__ import annotations

from typing import Any


def atr_from_candles(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """캔들 dict 리스트에서 ATR 계산. 봉 수 부족 시 None."""
    if len(candles) < period + 1:
        return None

    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    atr_val = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period

    return atr_val

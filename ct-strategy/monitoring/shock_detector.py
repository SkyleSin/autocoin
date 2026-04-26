"""뉴스 충격 감지 엔진 — 거래량/변동성/가격 갭 3가지 신호."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ShockLevel(Enum):
    NONE = "none"
    WARNING = "warning"    # 2/3 감지
    DANGER = "danger"      # 3/3 감지
    PANIC = "panic"        # 3/3 + 가격 갭 5%+


@dataclass(frozen=True)
class ShockSignal:
    level: ShockLevel
    volume_ratio: float           # 현재 거래량 / 평균 거래량
    volatility_ratio: float       # 현재 BB 폭 / 평균 BB 폭
    gap_pct: float                # 가격 갭 %
    triggered_signals: list[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=datetime.utcnow)


NO_SHOCK = ShockSignal(
    level=ShockLevel.NONE,
    volume_ratio=0.0,
    volatility_ratio=0.0,
    gap_pct=0.0,
)


def detect_volume_surge(
    candles_1m: list[dict[str, Any]],
    threshold: float = 3.0,
) -> tuple[bool, float]:
    """거래량 급등 감지. 최근 1분 vs 직전 20분 평균.

    Returns:
        (triggered, ratio)
    """
    if len(candles_1m) < 5:
        return False, 0.0

    current_vol = float(candles_1m[-1].get("candle_acc_trade_volume", 0))
    prev_vols = [
        float(c.get("candle_acc_trade_volume", 0))
        for c in candles_1m[-21:-1]
    ]
    if not prev_vols or sum(prev_vols) == 0:
        return False, 0.0

    avg_vol = sum(prev_vols) / len(prev_vols)
    if avg_vol == 0:
        return False, 0.0

    ratio = current_vol / avg_vol
    return ratio >= threshold, round(ratio, 2)


def detect_volatility_surge(
    candles_1m: list[dict[str, Any]],
    threshold: float = 2.0,
) -> tuple[bool, float]:
    """변동성 급등 감지. 최근 봉의 고-저 범위 vs 직전 20분 평균.

    Returns:
        (triggered, ratio)
    """
    if len(candles_1m) < 5:
        return False, 0.0

    latest = candles_1m[-1]
    current_range = abs(
        float(latest.get("high_price", 0)) - float(latest.get("low_price", 0))
    )

    prev_ranges = [
        abs(float(c.get("high_price", 0)) - float(c.get("low_price", 0)))
        for c in candles_1m[-21:-1]
    ]
    if not prev_ranges:
        return False, 0.0

    avg_range = sum(prev_ranges) / len(prev_ranges)
    if avg_range == 0:
        return False, 0.0

    ratio = current_range / avg_range
    return ratio >= threshold, round(ratio, 2)


def detect_price_gap(
    candles_1m: list[dict[str, Any]],
    threshold_pct: float = 2.0,
) -> tuple[bool, float]:
    """가격 갭 감지. 직전 종가 대비 현재 종가 변동률.

    Returns:
        (triggered, gap_pct) — gap_pct은 절대값
    """
    if len(candles_1m) < 2:
        return False, 0.0

    prev_close = float(candles_1m[-2].get("trade_price", 0))
    curr_close = float(candles_1m[-1].get("trade_price", 0))

    if prev_close == 0:
        return False, 0.0

    gap_pct = abs(curr_close - prev_close) / prev_close * 100
    return gap_pct >= threshold_pct, round(gap_pct, 2)


def classify_shock(
    vol_triggered: bool,
    vol_ratio: float,
    volat_triggered: bool,
    volat_ratio: float,
    gap_triggered: bool,
    gap_pct: float,
    panic_gap_pct: float = 5.0,
) -> ShockSignal:
    """3가지 신호를 종합하여 충격 강도 판정."""
    signals = []
    if vol_triggered:
        signals.append(f"거래량 {vol_ratio:.1f}배")
    if volat_triggered:
        signals.append(f"변동성 {volat_ratio:.1f}배")
    if gap_triggered:
        signals.append(f"가격갭 {gap_pct:.1f}%")

    count = len(signals)

    if count >= 3 and gap_pct >= panic_gap_pct:
        level = ShockLevel.PANIC
    elif count >= 3:
        level = ShockLevel.DANGER
    elif count >= 2:
        level = ShockLevel.WARNING
    else:
        level = ShockLevel.NONE

    return ShockSignal(
        level=level,
        volume_ratio=vol_ratio,
        volatility_ratio=volat_ratio,
        gap_pct=gap_pct,
        triggered_signals=signals,
    )

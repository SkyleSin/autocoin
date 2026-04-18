"""탑다운 MTF 전략 — S/R + MACD 체크 함수.

각 함수 시그니처: (snap, close_price, sr) -> bool
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.strategy.types import SR as SRAnalysis
    from core.strategy.types import Snap as IndicatorSnap


def chk_sr_support_bounce(
    _snap: IndicatorSnap, _close: float, sr: SRAnalysis | None,
) -> bool:
    return sr is not None and sr.signal == "SUPPORT_BOUNCE"


def chk_sr_resistance_break(
    _snap: IndicatorSnap, _close: float, sr: SRAnalysis | None,
) -> bool:
    return sr is not None and sr.signal == "RESISTANCE_BREAK"


def chk_sr_resistance_reject(
    _snap: IndicatorSnap, _close: float, sr: SRAnalysis | None,
) -> bool:
    return sr is not None and sr.signal == "RESISTANCE_REJECT"


def chk_sr_support_near(
    _snap: IndicatorSnap, _close: float, sr: SRAnalysis | None,
) -> bool:
    return (
        sr is not None
        and sr.distance_to_support_pct is not None
        and sr.distance_to_support_pct <= 0.3
    )


def chk_macd_golden(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    h, hp = snap.macd_histogram, snap.macd_histogram_prev
    return h is not None and hp is not None and h > 0 and hp <= 0


def chk_macd_dead(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    h, hp = snap.macd_histogram, snap.macd_histogram_prev
    return h is not None and hp is not None and h < 0 and hp >= 0


def chk_macd_hist_rising(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    h, hp = snap.macd_histogram, snap.macd_histogram_prev
    return h is not None and hp is not None and h > hp


def chk_macd_hist_falling(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    h, hp = snap.macd_histogram, snap.macd_histogram_prev
    return h is not None and hp is not None and h < hp

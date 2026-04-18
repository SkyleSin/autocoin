"""탑다운 MTF 전략 — BB/RSI/볼륨/SMA/EMA 체크 함수."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.strategy.types import SR as SRAnalysis
    from core.strategy.types import Snap as IndicatorSnap


def _bb_width(snap: object) -> float | None:
    """BB width = (upper - lower) / middle."""
    u, m, lo = snap.bb_upper, snap.bb_middle, snap.bb_lower  # type: ignore[attr-defined]
    if u is None or m is None or lo is None or m == 0:
        return None
    return (u - lo) / m


def chk_bb_squeeze(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    w = _bb_width(snap)
    return w is not None and w < 0.02


def chk_bb_expansion(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    w = _bb_width(snap)
    return w is not None and w > 0.08


def chk_bb_lower_break(
    snap: IndicatorSnap, close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.bb_lower is not None and close <= snap.bb_lower


def chk_rsi_lt35(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 < 35


def chk_rsi_lt40(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 < 40


def chk_rsi_lt45(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 < 45


def chk_rsi_lt50(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 < 50


def chk_rsi_gt50(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 > 50


def chk_rsi_gt55(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 > 55


def chk_rsi_gt60(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 > 60


def chk_rsi_gt65(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 > 65


def chk_rsi_gt70(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.rsi_14 is not None and snap.rsi_14 > 70


def chk_vol_low(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.volume_ratio is not None and snap.volume_ratio < 0.5


def chk_vol_high(
    snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None,
) -> bool:
    return snap.volume_ratio is not None and snap.volume_ratio >= 1.5



"""탑다운 MTF 전략 — SMA/EMA 체크 함수."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strategy.types import SR as SRAnalysis
    from strategy.types import Snap as IndicatorSnap


def chk_price_above_sma20(snap: IndicatorSnap, close: float, _sr: SRAnalysis | None) -> bool:
    return snap.sma_20 is not None and close > snap.sma_20


def chk_price_below_sma20(snap: IndicatorSnap, close: float, _sr: SRAnalysis | None) -> bool:
    return snap.sma_20 is not None and close < snap.sma_20


def chk_price_above_sma50(snap: IndicatorSnap, close: float, _sr: SRAnalysis | None) -> bool:
    return snap.sma_50 is not None and close > snap.sma_50


def chk_price_below_sma50(snap: IndicatorSnap, close: float, _sr: SRAnalysis | None) -> bool:
    return snap.sma_50 is not None and close < snap.sma_50


def chk_ema12_above_ema26(snap: IndicatorSnap, _close: float, _sr: SRAnalysis | None) -> bool:
    return (
        snap.ema_12 is not None
        and snap.ema_26 is not None
        and snap.ema_12 > snap.ema_26
    )

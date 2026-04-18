"""지표 스냅샷 복원 헬퍼 — API dict -> 경량 타입 변환."""

from __future__ import annotations

from typing import Any

from strategy.types import SR, Snap


def rebuild_snap(indicators: dict[str, Any]) -> Snap | None:
    """indicator_snapshots indicators dict -> Snap 복원."""
    try:
        return Snap(
            rsi_14=indicators.get("rsi_14"),
            macd=indicators.get("macd"),
            macd_signal=indicators.get("macd_signal"),
            macd_histogram=indicators.get("macd_histogram"),
            macd_histogram_prev=indicators.get("macd_histogram_prev"),
            bb_upper=indicators.get("bb_upper"),
            bb_middle=indicators.get("bb_middle"),
            bb_lower=indicators.get("bb_lower"),
            sma_20=indicators.get("sma_20"),
            sma_50=indicators.get("sma_50"),
            ema_12=indicators.get("ema_12"),
            ema_26=indicators.get("ema_26"),
            volume_ratio=indicators.get("volume_ratio"),
        )
    except Exception:
        return None


def rebuild_sr(sr_dict: dict[str, Any] | None) -> SR | None:
    """indicator_snapshots의 S/R dict -> SR 복원."""
    if not sr_dict:
        return None
    levels = [
        {
            "price": lv["price"],
            "type": lv["type"],
            "touches": lv["touches"],
            "strength": lv.get("strength", 0.5),
        }
        for lv in sr_dict.get("levels", [])
    ]
    return SR(
        levels=levels,
        nearest_support=sr_dict.get("nearest_support"),
        nearest_resistance=sr_dict.get("nearest_resistance"),
        signal=sr_dict.get("signal", "NONE"),
        distance_to_support_pct=sr_dict.get("distance_to_support_pct"),
        distance_to_resistance_pct=sr_dict.get("distance_to_resistance_pct"),
    )

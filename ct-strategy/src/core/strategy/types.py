"""경량 타입 정의 — ORM/분석 모듈 의존 없이 전략에서 사용."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Snap:
    """IndicatorSnapshot 경량 대체 — API dict에서 복원용."""

    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    macd_histogram_prev: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    ema_12: float | None = None
    ema_26: float | None = None
    volume_ratio: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        """dict 변환 (topdown_evaluate에서 사용)."""
        return {
            "rsi_14": self.rsi_14,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "macd_histogram_prev": self.macd_histogram_prev,
            "bb_upper": self.bb_upper,
            "bb_middle": self.bb_middle,
            "bb_lower": self.bb_lower,
            "sma_20": self.sma_20,
            "sma_50": self.sma_50,
            "ema_12": self.ema_12,
            "ema_26": self.ema_26,
            "volume_ratio": self.volume_ratio,
        }


@dataclass
class SR:
    """SRAnalysis 경량 대체 — API dict에서 복원용."""

    levels: list[dict[str, Any]] = field(default_factory=list)
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    signal: str = "NONE"
    distance_to_support_pct: float | None = None
    distance_to_resistance_pct: float | None = None


class AlertAction:
    """알림 판단 결과 상수."""

    SKIP = "skip"
    NEW = "new"
    CHANGED = "changed"
    REMIND = "remind"
    PASSED = "passed"
    CLEARED = "cleared"

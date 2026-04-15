"""ClosedTrade 데이터클래스 — 수익/MFE/MAE/품질 계산."""

from __future__ import annotations

from dataclasses import dataclass

from _topdown_util import clamp


@dataclass
class ClosedTrade:
    """청산된 트레이드."""
    market: str
    direction: str
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    entry_score: float
    fired: list[str]
    mfe_price: float
    mae_price: float
    profit_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    quality: float = 0.0

    def __post_init__(self):
        self.profit_pct = self._calc_profit()
        self.mfe_pct = self._calc_mfe()
        self.mae_pct = self._calc_mae()
        self.quality = self._calc_quality()

    def _calc_profit(self) -> float:
        e = self.entry_price
        if e == 0:
            return 0.0
        if self.direction == "BUY":
            return (self.exit_price - e) / e * 100
        return (e - self.exit_price) / e * 100

    def _calc_mfe(self) -> float:
        e = self.entry_price
        if e == 0:
            return 0.0
        if self.direction == "BUY":
            return max(0, (self.mfe_price - e) / e * 100)
        return max(0, (e - self.mfe_price) / e * 100)

    def _calc_mae(self) -> float:
        e = self.entry_price
        if e == 0:
            return 0.0
        if self.direction == "BUY":
            return max(0, (e - self.mae_price) / e * 100)
        return max(0, (self.mae_price - e) / e * 100)

    def _calc_quality(self) -> float:
        p = self.profit_pct / 100
        mfe = self.mfe_pct / 100
        mae = self.mae_pct / 100

        profit_score = clamp((p + 0.05) / 0.15, 0, 1)
        if mfe > 0:
            mfe_eff = clamp(p / mfe, 0, 1)
        else:
            mfe_eff = 1.0 if p >= 0 else 0.0
        mae_safety = clamp(1 - mae / 0.10, 0, 1)
        return profit_score * 0.4 + mfe_eff * 0.3 + mae_safety * 0.3

    @property
    def hit(self) -> bool:
        return self.profit_pct > 0

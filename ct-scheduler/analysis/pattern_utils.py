"""패턴 인식 공통 데이터클래스 및 유틸리티."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Trendline:
    """추세선 (최소자승법 피팅)."""
    slope: float
    intercept: float
    start_idx: int
    end_idx: int

    def price_at(self, idx: int) -> float:
        return self.slope * idx + self.intercept

    def to_dict(self) -> dict:
        return {
            "start_idx": self.start_idx, "end_idx": self.end_idx,
            "start_price": self.price_at(self.start_idx),
            "end_price": self.price_at(self.end_idx),
        }


@dataclass(frozen=True)
class PatternMatch:
    """탐지된 패턴."""
    name: str
    name_ko: str
    signal: str      # "BUY" | "SELL" | "HOLD"
    confidence: int  # 50~100
    description: str
    active: bool = True
    volume_confirmed: bool | None = None
    target_price: float | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name, "name_ko": self.name_ko,
            "signal": self.signal, "confidence": self.confidence,
            "description": self.description, "active": self.active,
        }
        if self.volume_confirmed is not None:
            d["volume_confirmed"] = self.volume_confirmed
        if self.target_price is not None:
            d["target_price"] = self.target_price
        return d


@dataclass(frozen=True)
class PatternAnalysis:
    """패턴 분석 결과."""
    patterns: list[PatternMatch]
    resistance_line: Trendline | None = None
    support_line: Trendline | None = None

    def to_dict(self) -> dict:
        result: dict = {"patterns": [p.to_dict() for p in self.patterns]}
        if self.resistance_line:
            result["resistance_trendline"] = self.resistance_line.to_dict()
        if self.support_line:
            result["support_trendline"] = self.support_line.to_dict()
        return result


def find_peaks(series: pd.Series, order: int = 5) -> list[int]:
    """로컬 최댓값 인덱스."""
    arr = series.values
    n = len(arr)
    peaks = []
    for i in range(order, n - order):
        if all(arr[i] > arr[i - j] for j in range(1, order + 1)) and all(
            arr[i] > arr[i + j] for j in range(1, order + 1)
        ):
            peaks.append(i)
    return peaks


def find_valleys(series: pd.Series, order: int = 5) -> list[int]:
    """로컬 최솟값 인덱스."""
    arr = series.values
    n = len(arr)
    valleys = []
    for i in range(order, n - order):
        if all(arr[i] < arr[i - j] for j in range(1, order + 1)) and all(
            arr[i] < arr[i + j] for j in range(1, order + 1)
        ):
            valleys.append(i)
    return valleys


def similar(a: float, b: float, tol_pct: float = 1.5) -> bool:
    """두 가격이 tol_pct% 이내로 비슷한지."""
    if a == 0:
        return False
    return abs(a - b) / a * 100 <= tol_pct

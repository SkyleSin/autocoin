"""차트 패턴 인식.

이미지 1~3 참고 패턴:
- 매도: 쌍봉, 삼중천정, 하락깃발, 상승쐐기, 머리어깨
- 매수: 쌍바닥, 트리플바닥, 상승깃발, 하락쐐기, 역머리어깨
- 중립: 삼각수렴(대칭), 박스권횡보
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PatternMatch:
    """탐지된 패턴."""

    name: str
    name_ko: str
    signal: str      # "BUY" | "SELL" | "HOLD"
    confidence: int  # 이미지 2 기준 신뢰도 (50~100)
    description: str


@dataclass(frozen=True)
class PatternAnalysis:
    """패턴 분석 결과."""

    patterns: list[PatternMatch]

    def to_dict(self) -> dict:
        return {
            "patterns": [
                {
                    "name": p.name,
                    "name_ko": p.name_ko,
                    "signal": p.signal,
                    "confidence": p.confidence,
                    "description": p.description,
                }
                for p in self.patterns
            ],
        }


def _find_peaks(series: pd.Series, order: int = 5) -> list[int]:
    """로컬 최댓값 인덱스."""
    arr = series.values
    n = len(arr)
    peaks = []
    for i in range(order, n - order):
        if all(arr[i] >= arr[i - j] for j in range(1, order + 1)) and all(
            arr[i] >= arr[i + j] for j in range(1, order + 1)
        ):
            peaks.append(i)
    return peaks


def _find_valleys(series: pd.Series, order: int = 5) -> list[int]:
    """로컬 최솟값 인덱스."""
    arr = series.values
    n = len(arr)
    valleys = []
    for i in range(order, n - order):
        if all(arr[i] <= arr[i - j] for j in range(1, order + 1)) and all(
            arr[i] <= arr[i + j] for j in range(1, order + 1)
        ):
            valleys.append(i)
    return valleys


def _similar(a: float, b: float, tol_pct: float = 1.0) -> bool:
    """두 가격이 tol_pct% 이내로 비슷한지."""
    if a == 0:
        return False
    return abs(a - b) / a * 100 <= tol_pct


def _detect_double_top(
    highs: pd.Series, closes: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """쌍봉 (Double Top) — 매도 100%."""
    peaks = _find_peaks(highs, order)
    if len(peaks) < 2:
        return None
    # 최근 두 고점
    p1, p2 = peaks[-2], peaks[-1]
    h1, h2 = float(highs.iloc[p1]), float(highs.iloc[p2])
    if not _similar(h1, h2, 1.5):
        return None
    # 두 고점 사이 저점이 있어야 함
    between_low = float(closes.iloc[p1:p2 + 1].min())
    neckline_drop = (h1 - between_low) / h1 * 100
    if neckline_drop < 1.0:
        return None
    # 현재가가 두 번째 고점 아래면 패턴 완성
    current = float(closes.iloc[-1])
    if current < h2:
        return PatternMatch(
            "double_top", "쌍봉", "SELL", 100,
            f"고점 {h1:,.0f}↔{h2:,.0f} 쌍봉 형성, 폭락 대비",
        )
    return None


def _detect_double_bottom(
    lows: pd.Series, closes: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """쌍바닥 (Double Bottom) — 매수."""
    valleys = _find_valleys(lows, order)
    if len(valleys) < 2:
        return None
    v1, v2 = valleys[-2], valleys[-1]
    l1, l2 = float(lows.iloc[v1]), float(lows.iloc[v2])
    if not _similar(l1, l2, 1.5):
        return None
    between_high = float(closes.iloc[v1:v2 + 1].max())
    neckline_rise = (between_high - l1) / l1 * 100
    if neckline_rise < 1.0:
        return None
    current = float(closes.iloc[-1])
    if current > l2:
        return PatternMatch(
            "double_bottom", "쌍바닥", "BUY", 80,
            f"저점 {l1:,.0f}↔{l2:,.0f} 쌍바닥 형성, 반등 예상",
        )
    return None


def _detect_triple_top(
    highs: pd.Series, closes: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """삼중천정 (Triple Top) — 매도."""
    peaks = _find_peaks(highs, order)
    if len(peaks) < 3:
        return None
    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    h1, h2, h3 = float(highs.iloc[p1]), float(highs.iloc[p2]), float(highs.iloc[p3])
    if _similar(h1, h2, 1.5) and _similar(h2, h3, 1.5):
        current = float(closes.iloc[-1])
        if current < h3:
            return PatternMatch(
                "triple_top", "삼중천정", "SELL", 100,
                f"고점 3회 저항 ({h1:,.0f}), 강한 매도 시그널",
            )
    return None


def _detect_triple_bottom(
    lows: pd.Series, closes: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """트리플바닥 (Triple Bottom) — 매수."""
    valleys = _find_valleys(lows, order)
    if len(valleys) < 3:
        return None
    v1, v2, v3 = valleys[-3], valleys[-2], valleys[-1]
    l1, l2, l3 = float(lows.iloc[v1]), float(lows.iloc[v2]), float(lows.iloc[v3])
    if _similar(l1, l2, 1.5) and _similar(l2, l3, 1.5):
        current = float(closes.iloc[-1])
        if current > l3:
            return PatternMatch(
                "triple_bottom", "트리플바닥", "BUY", 80,
                f"저점 3회 지지 ({l1:,.0f}), 강한 매수 시그널",
            )
    return None


def _detect_head_and_shoulders(
    highs: pd.Series, closes: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """머리어깨 (Head & Shoulders) — 매도."""
    peaks = _find_peaks(highs, order)
    if len(peaks) < 3:
        return None
    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    h1, h2, h3 = float(highs.iloc[p1]), float(highs.iloc[p2]), float(highs.iloc[p3])
    # 머리(h2)가 양 어깨(h1, h3)보다 높고, 양 어깨가 비슷해야 함
    if h2 > h1 and h2 > h3 and _similar(h1, h3, 2.0):
        head_ratio = (h2 - max(h1, h3)) / h2 * 100
        if head_ratio >= 1.0:
            current = float(closes.iloc[-1])
            if current < max(h1, h3):
                return PatternMatch(
                    "head_and_shoulders", "머리어깨", "SELL", 90,
                    f"머리 {h2:,.0f}, 어깨 {h1:,.0f}↔{h3:,.0f}",
                )
    return None


def _detect_inverse_head_and_shoulders(
    lows: pd.Series, closes: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """역머리어깨 (Inverse H&S) — 매수."""
    valleys = _find_valleys(lows, order)
    if len(valleys) < 3:
        return None
    v1, v2, v3 = valleys[-3], valleys[-2], valleys[-1]
    l1, l2, l3 = float(lows.iloc[v1]), float(lows.iloc[v2]), float(lows.iloc[v3])
    if l2 < l1 and l2 < l3 and _similar(l1, l3, 2.0):
        head_ratio = (min(l1, l3) - l2) / min(l1, l3) * 100
        if head_ratio >= 1.0:
            current = float(closes.iloc[-1])
            if current > min(l1, l3):
                return PatternMatch(
                    "inverse_head_and_shoulders", "역머리어깨", "BUY", 90,
                    f"역머리 {l2:,.0f}, 어깨 {l1:,.0f}↔{l3:,.0f}",
                )
    return None


def _detect_ascending_triangle(
    highs: pd.Series, lows: pd.Series, closes: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """상승삼각형 — 매수 65%."""
    peaks = _find_peaks(highs, order)
    valleys = _find_valleys(lows, order)
    if len(peaks) < 2 or len(valleys) < 2:
        return None
    # 고점 수평 (비슷), 저점 상승
    h_vals = [float(highs.iloc[p]) for p in peaks[-3:]]
    l_vals = [float(lows.iloc[v]) for v in valleys[-3:]]
    if len(h_vals) >= 2 and len(l_vals) >= 2:
        h_flat = all(_similar(h_vals[0], h, 1.0) for h in h_vals)
        l_rising = all(l_vals[i] < l_vals[i + 1] for i in range(len(l_vals) - 1))
        if h_flat and l_rising:
            return PatternMatch(
                "ascending_triangle", "상승삼각형", "BUY", 65,
                f"저항 {h_vals[-1]:,.0f} 수평, 저점 상승 중",
            )
    return None


def _detect_descending_triangle(
    highs: pd.Series, lows: pd.Series, closes: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """하락삼각형 — 매도 65%."""
    peaks = _find_peaks(highs, order)
    valleys = _find_valleys(lows, order)
    if len(peaks) < 2 or len(valleys) < 2:
        return None
    h_vals = [float(highs.iloc[p]) for p in peaks[-3:]]
    l_vals = [float(lows.iloc[v]) for v in valleys[-3:]]
    if len(h_vals) >= 2 and len(l_vals) >= 2:
        l_flat = all(_similar(l_vals[0], lv, 1.0) for lv in l_vals)
        h_falling = all(h_vals[i] > h_vals[i + 1] for i in range(len(h_vals) - 1))
        if l_flat and h_falling:
            return PatternMatch(
                "descending_triangle", "하락삼각형", "SELL", 65,
                f"지지 {l_vals[-1]:,.0f} 수평, 고점 하락 중",
            )
    return None


def _detect_bull_flag(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    lookback: int = 30,
) -> PatternMatch | None:
    """상승깃발 — 매수 80%."""
    if len(closes) < lookback:
        return None
    recent = closes.iloc[-lookback:]
    # 전반부 급등 (깃대)
    half = lookback // 2
    pole = recent.iloc[:half]
    flag = recent.iloc[half:]
    pole_gain = (float(pole.iloc[-1]) - float(pole.iloc[0])) / float(pole.iloc[0]) * 100
    # 후반부 소폭 하락 (깃발)
    flag_change = (float(flag.iloc[-1]) - float(flag.iloc[0])) / float(flag.iloc[0]) * 100
    if pole_gain >= 2.0 and -2.0 <= flag_change <= 0:
        return PatternMatch(
            "bull_flag", "상승깃발", "BUY", 80,
            f"급등 {pole_gain:.1f}% 후 조정 {flag_change:.1f}%, 재상승 예상",
        )
    return None


def _detect_bear_flag(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    lookback: int = 30,
) -> PatternMatch | None:
    """하락깃발 — 매도 80%."""
    if len(closes) < lookback:
        return None
    recent = closes.iloc[-lookback:]
    half = lookback // 2
    pole = recent.iloc[:half]
    flag = recent.iloc[half:]
    pole_drop = (float(pole.iloc[-1]) - float(pole.iloc[0])) / float(pole.iloc[0]) * 100
    flag_change = (float(flag.iloc[-1]) - float(flag.iloc[0])) / float(flag.iloc[0]) * 100
    if pole_drop <= -2.0 and 0 <= flag_change <= 2.0:
        return PatternMatch(
            "bear_flag", "하락깃발", "SELL", 80,
            f"급락 {pole_drop:.1f}% 후 반등 {flag_change:.1f}%, 재하락 예상",
        )
    return None


def _detect_rising_wedge(
    highs: pd.Series, lows: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """상승쐐기 — 매도."""
    peaks = _find_peaks(highs, order)
    valleys = _find_valleys(lows, order)
    if len(peaks) < 2 or len(valleys) < 2:
        return None
    h_vals = [float(highs.iloc[p]) for p in peaks[-3:]]
    l_vals = [float(lows.iloc[v]) for v in valleys[-3:]]
    if len(h_vals) >= 2 and len(l_vals) >= 2:
        h_rising = all(h_vals[i] < h_vals[i + 1] for i in range(len(h_vals) - 1))
        l_rising = all(l_vals[i] < l_vals[i + 1] for i in range(len(l_vals) - 1))
        if h_rising and l_rising:
            # 수렴: 고점 상승률 < 저점 상승률
            h_slope = (h_vals[-1] - h_vals[0]) / h_vals[0]
            l_slope = (l_vals[-1] - l_vals[0]) / l_vals[0]
            if 0 < h_slope < l_slope:
                return PatternMatch(
                    "rising_wedge", "상승쐐기형", "SELL", 75,
                    "고점·저점 모두 상승하나 수렴 중, 하락 전환 예상",
                )
    return None


def _detect_falling_wedge(
    highs: pd.Series, lows: pd.Series, order: int = 5,
) -> PatternMatch | None:
    """하락쐐기 — 매수."""
    peaks = _find_peaks(highs, order)
    valleys = _find_valleys(lows, order)
    if len(peaks) < 2 or len(valleys) < 2:
        return None
    h_vals = [float(highs.iloc[p]) for p in peaks[-3:]]
    l_vals = [float(lows.iloc[v]) for v in valleys[-3:]]
    if len(h_vals) >= 2 and len(l_vals) >= 2:
        h_falling = all(h_vals[i] > h_vals[i + 1] for i in range(len(h_vals) - 1))
        l_falling = all(l_vals[i] > l_vals[i + 1] for i in range(len(l_vals) - 1))
        if h_falling and l_falling:
            h_slope = abs(h_vals[-1] - h_vals[0]) / h_vals[0]
            l_slope = abs(l_vals[-1] - l_vals[0]) / l_vals[0]
            if 0 < h_slope > l_slope:
                return PatternMatch(
                    "falling_wedge", "하락쐐기형", "BUY", 75,
                    "고점·저점 모두 하락하나 수렴 중, 상승 전환 예상",
                )
    return None


def analyze(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    order: int = 5,
) -> PatternAnalysis:
    """OHLC 데이터에서 차트 패턴 탐지."""
    if len(closes) < 30:
        return PatternAnalysis([])

    detectors = [
        lambda: _detect_double_top(highs, closes, order),
        lambda: _detect_double_bottom(lows, closes, order),
        lambda: _detect_triple_top(highs, closes, order),
        lambda: _detect_triple_bottom(lows, closes, order),
        lambda: _detect_head_and_shoulders(highs, closes, order),
        lambda: _detect_inverse_head_and_shoulders(lows, closes, order),
        lambda: _detect_ascending_triangle(highs, lows, closes, order),
        lambda: _detect_descending_triangle(highs, lows, closes, order),
        lambda: _detect_bull_flag(highs, lows, closes),
        lambda: _detect_bear_flag(highs, lows, closes),
        lambda: _detect_rising_wedge(highs, lows, order),
        lambda: _detect_falling_wedge(highs, lows, order),
    ]

    patterns: list[PatternMatch] = []
    for detect in detectors:
        result = detect()
        if result is not None:
            patterns.append(result)

    # 신뢰도 높은 순 정렬
    patterns.sort(key=lambda p: p.confidence, reverse=True)
    return PatternAnalysis(patterns)

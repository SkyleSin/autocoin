"""추세선 기반 차트 패턴 인식 — 메인 진입점 (analyze 함수)."""
from __future__ import annotations

import pandas as pd

from analysis.pattern_flag import detect_bear_flag, detect_bull_flag
from analysis.pattern_helpers import fit_trendline, min_swing_pct, order_for_timeframe
from analysis.pattern_structural import detect_double_bottom, detect_double_top
from analysis.pattern_structural2 import (
    detect_head_and_shoulders,
    detect_inverse_head_and_shoulders,
)
from analysis.pattern_trendline import classify_trendline_patterns
from analysis.pattern_triple import detect_triple_bottom, detect_triple_top
from analysis.pattern_utils import (
    PatternAnalysis,
    PatternMatch,
    find_peaks,
    find_valleys,
)

_ACTIVE_PATTERNS: set[str] = {
    "ascending_triangle", "head_and_shoulders",
    "bear_flag", "bull_flag", "box_range", "symmetric_triangle",
}
_db_active_patterns: set[str] | None = None
_MAX_FIT_POINTS = 5

def set_active_patterns(names: set[str]) -> None:
    global _db_active_patterns  # noqa: PLW0603
    _db_active_patterns = names

def get_active_patterns() -> set[str]:
    return _db_active_patterns if _db_active_patterns is not None else _ACTIVE_PATTERNS

def _mark_active(patterns: list[PatternMatch]) -> list[PatternMatch]:
    active_set = get_active_patterns()
    return [
        PatternMatch(
            p.name, p.name_ko, p.signal, p.confidence, p.description,
            active=(p.name in active_set),
            volume_confirmed=p.volume_confirmed, target_price=p.target_price,
        ) for p in patterns
    ]

def _filter_conflicting(patterns: list[PatternMatch]) -> list[PatternMatch]:
    sigs = {p.signal for p in patterns if p.active}
    if "BUY" in sigs and "SELL" in sigs:
        return [p for p in patterns if not p.active or p.signal == "HOLD"]
    return patterns


def analyze(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    order: int | None = None, *, timeframe: str = "15m",
    min_swing_pct_val: float = 2.0, volumes: pd.Series | None = None,
) -> PatternAnalysis:
    """OHLC 데이터에서 차트 패턴 탐지."""
    if len(closes) < 30:
        return PatternAnalysis([])
    if min_swing_pct(highs, lows) < min_swing_pct_val:
        return PatternAnalysis([])
    if order is None:
        order = order_for_timeframe(timeframe)

    peaks = find_peaks(highs, order)
    valleys = find_valleys(lows, order)
    rp, rv = peaks[-_MAX_FIT_POINTS:], valleys[-_MAX_FIT_POINTS:]
    res = fit_trendline(rp, [float(highs.iloc[p]) for p in rp]) if len(rp) >= 2 else None
    sup = fit_trendline(rv, [float(lows.iloc[v]) for v in rv]) if len(rv) >= 2 else None

    patterns: list[PatternMatch] = []
    for det in [
        lambda: detect_double_top(highs, closes, order, volumes),
        lambda: detect_double_bottom(lows, closes, order, volumes),
        lambda: detect_triple_top(highs, closes, order, volumes),
        lambda: detect_triple_bottom(lows, closes, order, volumes),
        lambda: detect_head_and_shoulders(highs, closes, order, volumes),
        lambda: detect_inverse_head_and_shoulders(lows, closes, order, volumes),
    ]:
        r = det()
        if r:
            patterns.append(r)
    if res and sup:
        patterns.extend(
            classify_trendline_patterns(res, sup, closes, float(closes.mean()), volumes),
        )
    for flag_det in [detect_bull_flag, detect_bear_flag]:
        r = flag_det(highs, lows, closes, peaks, valleys)
        if r:
            patterns.append(r)

    patterns = _mark_active(patterns)
    active_sigs = {p.signal for p in patterns if p.active}
    if "BUY" in active_sigs and "SELL" in active_sigs and order < 20:
        return analyze(
            highs, lows, closes, order=order + 5,
            timeframe=timeframe, min_swing_pct_val=min_swing_pct_val, volumes=volumes,
        )
    patterns = _filter_conflicting(patterns)
    patterns.sort(key=lambda p: p.confidence, reverse=True)
    return PatternAnalysis(patterns=patterns, resistance_line=res, support_line=sup)

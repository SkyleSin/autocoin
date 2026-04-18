"""구조적 패턴: 쌍봉, 쌍바닥."""
from __future__ import annotations

import pandas as pd

from .pattern_helpers import (
    check_min_max_distance,
    check_volume,
    has_prior_downtrend,
    has_prior_uptrend,
)
from .pattern_utils import PatternMatch, find_peaks, find_valleys, similar


def detect_double_top(
    highs: pd.Series, closes: pd.Series, order: int,
    volumes: pd.Series | None = None,
) -> PatternMatch | None:
    """쌍봉 — 고점 2회 + M자 형태 + 넥라인 이탈. 선행 상승추세 시 신뢰도 상승."""
    peaks = find_peaks(highs, order)
    if len(peaks) < 2:
        return None
    p1, p2 = peaks[-2], peaks[-1]
    h1, h2 = float(highs.iloc[p1]), float(highs.iloc[p2])
    if not similar(h1, h2, 1.5):
        return None
    n = len(closes)
    if not check_min_max_distance(p1, p2, order, n):
        return None
    # 선행 추세: 없어도 감지하되 confidence 차등
    conf = 100 if has_prior_uptrend(closes, p1, lookback=30) else 75
    neckline = float(closes.iloc[p1:p2 + 1].min())
    peak_avg = (h1 + h2) / 2
    neckline_drop = (peak_avg - neckline) / peak_avg * 100
    if neckline_drop < 2.0:
        return None
    current = float(closes.iloc[-1])
    if current >= neckline:
        return None
    vol = check_volume(volumes, len(closes) - 1)
    target = neckline - (peak_avg - neckline)
    desc = f"고점 {h1:,.0f}↔{h2:,.0f}, 넥라인 {neckline:,.0f} 이탈"
    if conf < 100:
        desc += " (선행추세 미확인)"
    return PatternMatch(
        "double_top", "쌍봉", "SELL", conf, desc,
        volume_confirmed=vol, target_price=target,
    )


def detect_double_bottom(
    lows: pd.Series, closes: pd.Series, order: int,
    volumes: pd.Series | None = None,
) -> PatternMatch | None:
    """쌍바닥 — 하락추세 후 저점 2회 + W자 형태 + 넥라인 돌파."""
    valleys = find_valleys(lows, order)
    if len(valleys) < 2:
        return None
    v1, v2 = valleys[-2], valleys[-1]
    l1, l2 = float(lows.iloc[v1]), float(lows.iloc[v2])
    if not similar(l1, l2, 1.5):
        return None
    n = len(closes)
    if not check_min_max_distance(v1, v2, order, n):
        return None
    conf = 80 if has_prior_downtrend(closes, v1, lookback=30) else 60
    neckline = float(closes.iloc[v1:v2 + 1].max())
    valley_avg = (l1 + l2) / 2
    neckline_rise = (neckline - valley_avg) / valley_avg * 100
    if neckline_rise < 2.0:
        return None
    current = float(closes.iloc[-1])
    if current <= neckline:
        return None
    vol = check_volume(volumes, len(closes) - 1)
    target = neckline + (neckline - valley_avg)
    desc = f"저점 {l1:,.0f}↔{l2:,.0f}, 넥라인 {neckline:,.0f} 돌파"
    if conf < 80:
        desc += " (선행추세 미확인)"
    return PatternMatch(
        "double_bottom", "쌍바닥", "BUY", conf, desc,
        volume_confirmed=vol, target_price=target,
    )

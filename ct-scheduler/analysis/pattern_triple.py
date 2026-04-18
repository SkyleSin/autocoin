"""구조적 패턴: 삼중천정, 트리플바닥."""
from __future__ import annotations

import pandas as pd

from .pattern_helpers import check_volume
from .pattern_utils import PatternMatch, find_peaks, find_valleys, similar


def detect_triple_top(
    highs: pd.Series, closes: pd.Series, order: int,
    volumes: pd.Series | None = None,
) -> PatternMatch | None:
    """삼중천정 — 고점 3회 + 거리 제한 + 넥라인 이탈."""
    peaks = find_peaks(highs, order)
    if len(peaks) < 3:
        return None
    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    h1, h2, h3 = float(highs.iloc[p1]), float(highs.iloc[p2]), float(highs.iloc[p3])
    if not (similar(h1, h2, 1.5) and similar(h2, h3, 1.5)):
        return None
    n = len(closes)
    min_dist = order * 2
    if (p2 - p1) < min_dist or (p3 - p2) < min_dist:
        return None
    if (p3 - p1) > n * 0.6:
        return None
    valley_1 = float(closes.iloc[p1:p2 + 1].min())
    valley_2 = float(closes.iloc[p2:p3 + 1].min())
    neckline = max(valley_1, valley_2)
    if (h1 - neckline) / h1 * 100 < 1.5:
        return None
    current = float(closes.iloc[-1])
    if current >= neckline:
        return None
    vol = check_volume(volumes, n - 1)
    target = neckline - (h1 - neckline)
    return PatternMatch(
        "triple_top", "삼중천정", "SELL", 100,
        f"고점 3회 저항 ({h1:,.0f}), 넥라인 {neckline:,.0f} 이탈",
        volume_confirmed=vol, target_price=target,
    )


def detect_triple_bottom(
    lows: pd.Series, closes: pd.Series, order: int,
    volumes: pd.Series | None = None,
) -> PatternMatch | None:
    """트리플바닥 — 저점 3회 + 거리 제한 + 넥라인 돌파."""
    valleys = find_valleys(lows, order)
    if len(valleys) < 3:
        return None
    v1, v2, v3 = valleys[-3], valleys[-2], valleys[-1]
    l1, l2, l3 = float(lows.iloc[v1]), float(lows.iloc[v2]), float(lows.iloc[v3])
    if not (similar(l1, l2, 1.5) and similar(l2, l3, 1.5)):
        return None
    n = len(closes)
    min_dist = order * 2
    if (v2 - v1) < min_dist or (v3 - v2) < min_dist:
        return None
    if (v3 - v1) > n * 0.6:
        return None
    peak_1 = float(closes.iloc[v1:v2 + 1].max())
    peak_2 = float(closes.iloc[v2:v3 + 1].max())
    neckline = min(peak_1, peak_2)
    if (neckline - l1) / l1 * 100 < 1.5:
        return None
    current = float(closes.iloc[-1])
    if current <= neckline:
        return None
    vol = check_volume(volumes, n - 1)
    target = neckline + (neckline - l1)
    return PatternMatch(
        "triple_bottom", "트리플바닥", "BUY", 80,
        f"저점 3회 지지 ({l1:,.0f}), 넥라인 {neckline:,.0f} 돌파",
        volume_confirmed=vol, target_price=target,
    )

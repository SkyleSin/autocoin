"""구조적 패턴: 머리어깨, 역머리어깨."""
from __future__ import annotations

import pandas as pd

from .pattern_helpers import check_volume, has_prior_downtrend, has_prior_uptrend
from .pattern_utils import PatternMatch, find_peaks, find_valleys, similar


def detect_head_and_shoulders(
    highs: pd.Series, closes: pd.Series, order: int,
    volumes: pd.Series | None = None,
) -> PatternMatch | None:
    """머리어깨 — 거리 제한 + 넥라인 이탈. 선행 상승추세 시 신뢰도 상승."""
    peaks = find_peaks(highs, order)
    if len(peaks) < 3:
        return None
    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    h1, h2, h3 = float(highs.iloc[p1]), float(highs.iloc[p2]), float(highs.iloc[p3])
    if not (h2 > h1 and h2 > h3 and similar(h1, h3, 2.0)):
        return None
    if (h2 - max(h1, h3)) / h2 * 100 < 1.0:
        return None
    n = len(closes)
    min_dist = max(order, 5)
    if (p2 - p1) < min_dist or (p3 - p2) < min_dist:
        return None
    # 선행 추세: 없어도 감지하되 confidence 차등
    conf = 90 if has_prior_uptrend(closes, p1, lookback=30) else 70
    valley_l = float(closes.iloc[p1:p2 + 1].min())
    valley_r = float(closes.iloc[p2:p3 + 1].min())
    neckline = max(valley_l, valley_r)
    current = float(closes.iloc[-1])
    if current >= neckline:
        return None
    vol = check_volume(volumes, n - 1)
    target = neckline - (h2 - neckline)
    desc = f"머리 {h2:,.0f}, 어깨 {h1:,.0f}↔{h3:,.0f}, 넥라인 {neckline:,.0f} 이탈"
    if conf < 90:
        desc += " (선행추세 미확인)"
    return PatternMatch(
        "head_and_shoulders", "머리어깨", "SELL", conf, desc,
        volume_confirmed=vol, target_price=target,
    )


def detect_inverse_head_and_shoulders(
    lows: pd.Series, closes: pd.Series, order: int,
    volumes: pd.Series | None = None,
) -> PatternMatch | None:
    """역머리어깨 — 거리 제한 + 넥라인 돌파. 선행 하락추세 시 신뢰도 상승."""
    valleys = find_valleys(lows, order)
    if len(valleys) < 3:
        return None
    v1, v2, v3 = valleys[-3], valleys[-2], valleys[-1]
    l1, l2, l3 = float(lows.iloc[v1]), float(lows.iloc[v2]), float(lows.iloc[v3])
    if not (l2 < l1 and l2 < l3 and similar(l1, l3, 2.0)):
        return None
    if (min(l1, l3) - l2) / min(l1, l3) * 100 < 1.0:
        return None
    n = len(closes)
    min_dist = max(order, 5)
    if (v2 - v1) < min_dist or (v3 - v2) < min_dist:
        return None
    conf = 90 if has_prior_downtrend(closes, v1, lookback=30) else 70
    peak_l = float(closes.iloc[v1:v2 + 1].max())
    peak_r = float(closes.iloc[v2:v3 + 1].max())
    neckline = min(peak_l, peak_r)
    current = float(closes.iloc[-1])
    if current <= neckline:
        return None
    vol = check_volume(volumes, n - 1)
    target = neckline + (neckline - l2)
    desc = f"역머리 {l2:,.0f}, 어깨 {l1:,.0f}↔{l3:,.0f}, 넥라인 {neckline:,.0f} 돌파"
    if conf < 90:
        desc += " (선행추세 미확인)"
    return PatternMatch(
        "inverse_head_and_shoulders", "역머리어깨", "BUY", conf, desc,
        volume_confirmed=vol, target_price=target,
    )

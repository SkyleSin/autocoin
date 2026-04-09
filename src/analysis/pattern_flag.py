"""깃발 패턴: 상승깃발, 하락깃발 (채널 형태 검증 강화)."""
from __future__ import annotations

import pandas as pd

from analysis.pattern_helpers import fit_trendline
from analysis.pattern_utils import PatternMatch

_MIN_POLE_PCT, _MAX_POLE_BARS = 1.5, 15
_MIN_FLAG_BARS, _MAX_FLAG_RATIO = 3, 5.0

def _is_parallel_channel(highs: pd.Series, lows: pd.Series, start: int) -> bool:
    """깃발 구간 고점/저점 추세선이 평행채널인지."""
    fh, fl = highs.iloc[start:], lows.iloc[start:]
    if len(fh) < _MIN_FLAG_BARS:
        return False
    idx = list(range(len(fh)))
    hi = fit_trendline(idx, [float(v) for v in fh])
    lo = fit_trendline(idx, [float(v) for v in fl])
    if hi is None or lo is None:
        return False
    avg = (abs(hi.slope) + abs(lo.slope)) / 2
    return avg == 0 or abs(hi.slope - lo.slope) / avg < 1.0

def _valid_pole_flag(pole_bars: int, flag_len: int) -> bool:
    return 1 <= pole_bars <= _MAX_POLE_BARS and _MIN_FLAG_BARS <= flag_len <= pole_bars * _MAX_FLAG_RATIO  # noqa: E501

def detect_bull_flag(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    peaks: list[int], valleys: list[int],
) -> PatternMatch | None:
    """상승깃발 — 급등 후 소폭 하락 평행채널."""
    if not peaks or not valleys:
        return None
    top_idx = peaks[-1]
    top = float(highs.iloc[top_idx])
    pre_v = [v for v in valleys if v < top_idx]
    if not pre_v:
        return None
    bot_idx, bot = pre_v[-1], float(lows.iloc[pre_v[-1]])
    gain = (top - bot) / bot * 100
    if gain < _MIN_POLE_PCT:
        return None
    pole_bars = top_idx - bot_idx
    flag = closes.iloc[top_idx:]
    if not _valid_pole_flag(pole_bars, len(flag)):
        return None
    fs, fe = float(flag.iloc[0]), float(flag.iloc[-1])
    fc = (fe - fs) / fs * 100
    if not (-3.0 <= fc <= 0.5):
        return None
    fl = float(lows.iloc[top_idx:].min())
    if (top - fl) / (top - bot) > 0.5:
        return None
    if not _is_parallel_channel(highs, lows, top_idx):
        return None
    return PatternMatch(
        "bull_flag", "상승깃발", "BUY", 80,
        f"급등 {gain:.1f}% 후 조정 {fc:.1f}%, 재상승 예상",
        target_price=fe + (top - bot),
    )


def detect_bear_flag(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    peaks: list[int], valleys: list[int],
) -> PatternMatch | None:
    """하락깃발 — 급락 후 소폭 반등 평행채널."""
    if not peaks or not valleys:
        return None
    bot_idx = valleys[-1]
    bot = float(lows.iloc[bot_idx])
    pre_p = [p for p in peaks if p < bot_idx]
    if not pre_p:
        return None
    top_idx, top = pre_p[-1], float(highs.iloc[pre_p[-1]])
    drop = (bot - top) / top * 100
    if drop > -_MIN_POLE_PCT:
        return None
    pole_bars = bot_idx - top_idx
    flag = closes.iloc[bot_idx:]
    if not _valid_pole_flag(pole_bars, len(flag)):
        return None
    fs, fe = float(flag.iloc[0]), float(flag.iloc[-1])
    fc = (fe - fs) / fs * 100
    if not (-0.5 <= fc <= 3.0):
        return None
    fh = float(highs.iloc[bot_idx:].max())
    if (fh - bot) / (top - bot) > 0.5:
        return None
    if not _is_parallel_channel(highs, lows, bot_idx):
        return None
    return PatternMatch(
        "bear_flag", "하락깃발", "SELL", 80,
        f"급락 {drop:.1f}% 후 반등 {fc:.1f}%, 재하락 예상",
        target_price=fe - (top - bot),
    )

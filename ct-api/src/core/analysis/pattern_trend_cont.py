"""추세 지속 패턴: 강세 추세 지속, 약세 추세 지속.

고점·저점이 함께 높아지는(강세) 또는 낮아지는(약세) 패턴.
추세선 하단 반등 또는 상단 저항에서 시그널 발생.
"""
from __future__ import annotations

import pandas as pd

from .pattern_helpers import check_volume, fit_trendline
from .pattern_utils import PatternMatch


def detect_bullish_continuation(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    peaks: list[int], valleys: list[int],
    volumes: pd.Series | None = None,
) -> PatternMatch | None:
    """강세 추세 지속 — 최근 피크·밸리 3개 이상 연속 상승 + 추세선 근처 반등."""
    if len(peaks) < 3 or len(valleys) < 3:
        return None

    # 최근 3개 피크/밸리가 연속 상승하는지
    rp = peaks[-3:]
    rv = valleys[-3:]
    peak_vals = [float(highs.iloc[p]) for p in rp]
    valley_vals = [float(lows.iloc[v]) for v in rv]

    if not (peak_vals[0] < peak_vals[1] < peak_vals[2]):
        return None
    if not (valley_vals[0] < valley_vals[1] < valley_vals[2]):
        return None

    # 추세선 피팅 (저점 기준)
    sup_line = fit_trendline(rv, valley_vals)
    if sup_line is None:
        return None

    # 현재가가 추세선 근처에서 반등 중인지 (추세선 위 2% 이내)
    current = float(closes.iloc[-1])
    last_idx = len(closes) - 1
    support_at = sup_line.price_at(last_idx)

    if support_at <= 0:
        return None
    dist_pct = (current - support_at) / support_at * 100

    # 추세선 위 0~3% 범위에서 반등 중이면 매수 시그널
    if not (0 <= dist_pct <= 3.0):
        return None

    # 직전 봉보다 현재가가 높아야 반등 확인
    if len(closes) < 2:
        return None
    prev = float(closes.iloc[-2])
    if current <= prev:
        return None

    vol = check_volume(volumes, last_idx)
    # 목표가: 최근 고점 수준
    target = peak_vals[-1]

    return PatternMatch(
        "bullish_continuation", "강세추세지속", "BUY", 60,
        f"고점·저점 연속 상승, 추세선 {support_at:,.0f} 부근 반등",
        volume_confirmed=vol, target_price=target,
    )


def detect_bearish_continuation(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    peaks: list[int], valleys: list[int],
    volumes: pd.Series | None = None,
) -> PatternMatch | None:
    """약세 추세 지속 — 최근 피크·밸리 3개 이상 연속 하락 + 추세선 근처 저항."""
    if len(peaks) < 3 or len(valleys) < 3:
        return None

    rp = peaks[-3:]
    rv = valleys[-3:]
    peak_vals = [float(highs.iloc[p]) for p in rp]
    valley_vals = [float(lows.iloc[v]) for v in rv]

    if not (peak_vals[0] > peak_vals[1] > peak_vals[2]):
        return None
    if not (valley_vals[0] > valley_vals[1] > valley_vals[2]):
        return None

    # 추세선 피팅 (고점 기준)
    res_line = fit_trendline(rp, peak_vals)
    if res_line is None:
        return None

    current = float(closes.iloc[-1])
    last_idx = len(closes) - 1
    resist_at = res_line.price_at(last_idx)

    if resist_at <= 0:
        return None
    dist_pct = (resist_at - current) / resist_at * 100

    # 추세선 아래 0~3% 범위에서 저항 중이면 매도 시그널
    if not (0 <= dist_pct <= 3.0):
        return None

    # 직전 봉보다 현재가가 낮아야 저항 확인
    if len(closes) < 2:
        return None
    prev = float(closes.iloc[-2])
    if current >= prev:
        return None

    vol = check_volume(volumes, last_idx)
    target = valley_vals[-1]

    return PatternMatch(
        "bearish_continuation", "약세추세지속", "SELL", 60,
        f"고점·저점 연속 하락, 추세선 {resist_at:,.0f} 부근 저항",
        volume_confirmed=vol, target_price=target,
    )

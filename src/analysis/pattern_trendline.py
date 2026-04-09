"""추세선 기반 패턴: 삼각형, 쐐기, 박스권."""

from __future__ import annotations

import pandas as pd

from analysis.pattern_helpers import check_volume, slope_pct
from analysis.pattern_utils import PatternMatch, Trendline

_FLAT_THRESHOLD = 0.01  # 봉당 0.01% 미만이면 수평


def classify_trendline_patterns(
    res_line: Trendline,
    sup_line: Trendline,
    closes: pd.Series,
    avg_price: float,
    volumes: pd.Series | None = None,
) -> list[PatternMatch]:
    """저항/지지 추세선 기울기 조합으로 패턴 분류 (점진적 수렴 검증)."""
    res_pct = slope_pct(res_line, avg_price)
    sup_pct = slope_pct(sup_line, avg_price)

    res_flat = abs(res_pct) < _FLAT_THRESHOLD
    sup_flat = abs(sup_pct) < _FLAT_THRESHOLD
    res_rising = res_pct >= _FLAT_THRESHOLD
    res_falling = res_pct <= -_FLAT_THRESHOLD
    sup_rising = sup_pct >= _FLAT_THRESHOLD
    sup_falling = sup_pct <= -_FLAT_THRESHOLD

    # 점진적 수렴: 시작→중간→끝 갭이 순차적으로 줄어야 함
    gap_start = res_line.price_at(res_line.start_idx) - sup_line.price_at(sup_line.start_idx)
    gap_end = res_line.price_at(res_line.end_idx) - sup_line.price_at(sup_line.end_idx)
    mid_idx = (res_line.start_idx + res_line.end_idx) // 2
    gap_mid = res_line.price_at(mid_idx) - sup_line.price_at(mid_idx)
    converging = (
        gap_start > 0
        and gap_mid < gap_start * 0.85
        and gap_end < gap_mid * 0.85
        and gap_end > 0
    )

    current = float(closes.iloc[-1])
    last_idx = len(closes) - 1
    res_current = res_line.price_at(last_idx)
    sup_current = sup_line.price_at(last_idx)
    vol = check_volume(volumes, last_idx)

    patterns: list[PatternMatch] = []

    # 삼각형 밑변 높이 (목표가 계산용)
    base_height = gap_start

    if res_flat and sup_rising:
        if current > res_current:
            target = current + base_height
            patterns.append(PatternMatch(
                "ascending_triangle", "상승삼각형", "BUY", 65,
                f"저항 {res_current:,.0f} 수평, 저점 상승 → 돌파",
                volume_confirmed=vol, target_price=target,
            ))
    elif res_falling and sup_flat:
        if current < sup_current:
            target = current - base_height
            patterns.append(PatternMatch(
                "descending_triangle", "하락삼각형", "SELL", 65,
                f"지지 {sup_current:,.0f} 수평, 고점 하락 → 이탈",
                volume_confirmed=vol, target_price=target,
            ))
    elif res_falling and sup_rising and converging:
        patterns.append(PatternMatch(
            "symmetric_triangle", "대칭삼각형", "HOLD", 50,
            "고점 하락 + 저점 상승 → 수렴 중, 방향 미확정",
        ))
    elif res_rising and sup_rising and converging:
        pass  # 상승쐐기: 비활성 (백테스트 적중률 0~23%)
    elif res_falling and sup_falling and converging:
        # 하락쐐기: 저항선이 지지선보다 더 가파르게 하락해야 수렴
        if abs(res_pct) > abs(sup_pct) and current > res_current:
            patterns.append(PatternMatch(
                "falling_wedge", "하락쐐기형", "BUY", 75,
                "고점·저점 하락하며 수렴, 상단 돌파 → 상승 전환",
                volume_confirmed=vol,
            ))
    elif res_flat and sup_flat:
        patterns.append(PatternMatch(
            "box_range", "박스권횡보", "HOLD", 50,
            f"저항 {res_current:,.0f} ~ 지지 {sup_current:,.0f} 박스권",
        ))

    return patterns

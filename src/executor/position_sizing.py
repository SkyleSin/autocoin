"""패턴 기반 포지션 사이징 — 패턴별 투자 비율 + 리스크 자동 조절."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 패턴별 기본 투자 비율 (KRW 잔고 대비 %)
# 이미지 참고 신뢰도 기반:
#   100% 신뢰 패턴 → 높은 비율
#   65% 신뢰 패턴 → 중간 비율
#   50% 이하 → 낮은 비율
PATTERN_ALLOCATION: dict[str, float] = {
    # 매수 패턴
    "double_bottom": 25.0,              # 쌍바닥 — 80% 신뢰
    "triple_bottom": 30.0,              # 트리플바닥 — 강한 매수
    "inverse_head_and_shoulders": 35.0, # 역머리어깨 — 매우 강한 매수
    "ascending_triangle": 20.0,         # 상승삼각형 — 65% 신뢰
    "bull_flag": 30.0,                  # 상승깃발 — 80% 신뢰
    "falling_wedge": 25.0,              # 하락쐐기 — 반전 매수
    # 매도 패턴
    "double_top": 30.0,                 # 쌍봉 — 100% 신뢰, 강한 매도
    "triple_top": 35.0,                 # 삼중천정 — 100% 신뢰
    "head_and_shoulders": 35.0,         # 머리어깨 — 90% 신뢰
    "descending_triangle": 20.0,        # 하락삼각형 — 65% 신뢰
    "bear_flag": 30.0,                  # 하락깃발 — 80% 신뢰
    "rising_wedge": 25.0,               # 상승쐐기 — 반전 매도
}

# 패턴 없을 때 기본 비율
DEFAULT_ALLOCATION_PCT = 10.0

# 자동 조절 기준
MAX_ALLOCATION_PCT = 50.0   # 절대 상한
MIN_ALLOCATION_PCT = 5.0    # 절대 하한


@dataclass(frozen=True)
class PositionSize:
    """포지션 사이징 결과."""

    allocation_pct: float   # 최종 투자 비율 (%)
    amount_krw: float       # 투자 금액 (KRW)
    pattern_name: str       # 기준 패턴
    adjustments: list[str]  # 조절 사유


def calculate_position(
    krw_balance: float,
    *,
    patterns: list[dict[str, Any]] | None = None,
    confidence: float = 0.5,
    indicators: dict[str, Any] | None = None,
) -> PositionSize:
    """패턴 + 시장 상황 기반 포지션 사이징.

    1. 패턴별 기본 비율 결정
    2. 리스크 조건으로 자동 조절 (과한 금액 방지)
    """
    adjustments: list[str] = []

    # 1) 패턴별 기본 비율
    base_pct = DEFAULT_ALLOCATION_PCT
    pattern_name = "없음"

    if patterns:
        # 가장 신뢰도 높은 패턴 기준
        top = patterns[0]
        name = top.get("name", "")
        pattern_name = top.get("name_ko", name)
        base_pct = PATTERN_ALLOCATION.get(name, DEFAULT_ALLOCATION_PCT)
        adjustments.append(f"패턴 {pattern_name} 기본 {base_pct}%")

    # 2) confidence 스케일링 (0.5~1.0 → 50~100%)
    conf_scale = max(0.5, min(confidence, 1.0))
    adjusted_pct = base_pct * conf_scale
    if conf_scale < 1.0:
        adjustments.append(f"confidence {confidence:.0%} → {conf_scale:.0%} 스케일")

    # 3) RSI 기반 조절
    if indicators:
        rsi = indicators.get("rsi_14")
        if rsi is not None:
            if rsi < 20:
                # 극단적 과매도 → 비율 상향 (바닥 매수 기회)
                adjusted_pct *= 1.2
                adjustments.append(f"RSI {rsi:.0f} 극과매도 +20%")
            elif rsi > 80:
                # 극단적 과매수 → 비율 하향 (위험)
                adjusted_pct *= 0.6
                adjustments.append(f"RSI {rsi:.0f} 극과매수 -40%")
            elif 60 < rsi < 70:
                # 과매수 접근 → 소폭 하향
                adjusted_pct *= 0.8
                adjustments.append(f"RSI {rsi:.0f} 과매수 접근 -20%")

    # 4) 볼린저밴드 위치 조절
    if indicators:
        bb_upper = indicators.get("bb_upper")
        bb_lower = indicators.get("bb_lower")
        bb_middle = indicators.get("bb_middle")
        if bb_upper and bb_lower and bb_middle:
            bb_width = (bb_upper - bb_lower) / bb_middle * 100
            if bb_width < 1.0:
                # 밴드 수축 → 큰 변동 예고, 비율 하향 (방향 불확실)
                adjusted_pct *= 0.7
                adjustments.append(f"볼린저 수축 ({bb_width:.1f}%) -30%")

    # 5) 상한/하한 클램핑
    if adjusted_pct > MAX_ALLOCATION_PCT:
        adjustments.append(f"상한 적용 {adjusted_pct:.1f}% → {MAX_ALLOCATION_PCT}%")
        adjusted_pct = MAX_ALLOCATION_PCT
    if adjusted_pct < MIN_ALLOCATION_PCT:
        adjustments.append(f"하한 적용 {adjusted_pct:.1f}% → {MIN_ALLOCATION_PCT}%")
        adjusted_pct = MIN_ALLOCATION_PCT

    # 6) 금액 계산
    amount = krw_balance * (adjusted_pct / 100)

    # 7) 잔고 대비 현실성 체크 — 잔고가 부족하면 최대 가용액
    if amount > krw_balance * 0.95:
        amount = krw_balance * 0.95
        adjusted_pct = amount / krw_balance * 100
        adjustments.append("잔고 95% 상한 적용")

    return PositionSize(
        allocation_pct=round(adjusted_pct, 1),
        amount_krw=round(amount, 0),
        pattern_name=pattern_name,
        adjustments=adjustments,
    )

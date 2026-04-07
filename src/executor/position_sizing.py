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

# 매도 패턴별 추천 매도 비율 (보유량 대비 %)
SELL_PATTERN_RECOMMENDATION: dict[str, int] = {
    "double_top": 100,          # 쌍봉 — 전량 매도 추천
    "triple_top": 100,          # 삼중천정 — 전량 매도
    "head_and_shoulders": 80,   # 머리어깨 — 80% 매도
    "bear_flag": 80,            # 하락깃발 — 80% 매도
    "rising_wedge": 60,         # 상승쐐기 — 60% 매도
    "descending_triangle": 60,  # 하락삼각형 — 60% 매도
}
SELL_DEFAULT_RECOMMENDATION = 40  # 패턴 없을 때 기본 추천

# 자동 조절 기준
MIN_ALLOCATION_PCT = 3.0    # 절대 하한 (매수용)


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
    max_per_trade_pct: float = 15.0,
) -> PositionSize:
    """패턴 + 시장 상황 기반 포지션 사이징.

    max_per_trade_pct 내에서 패턴/confidence/지표로 비율 결정.
    """
    adjustments: list[str] = []

    # 1) 패턴별 기본 비율 → max_per_trade_pct 대비 비율로 스케일
    pattern_name = "없음"
    pattern_weight = 0.5  # 패턴 없으면 상한의 50%

    if patterns:
        top = patterns[0]
        name = top.get("name", "")
        pattern_name = top.get("name_ko", name)
        raw_pct = PATTERN_ALLOCATION.get(name, DEFAULT_ALLOCATION_PCT)
        # 패턴 기본 비율(10~35%)을 0.3~1.0 가중치로 변환
        pattern_weight = max(0.3, min(raw_pct / 35.0, 1.0))
        adjustments.append(f"패턴 {pattern_name} 가중치 {pattern_weight:.0%}")

    base_pct = max_per_trade_pct * pattern_weight

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
                adjusted_pct *= 1.2
                adjustments.append(f"RSI {rsi:.0f} 극과매도 +20%")
            elif rsi > 80:
                adjusted_pct *= 0.6
                adjustments.append(f"RSI {rsi:.0f} 극과매수 -40%")
            elif 60 < rsi < 70:
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
                adjusted_pct *= 0.7
                adjustments.append(f"볼린저 수축 ({bb_width:.1f}%) -30%")

    # 5) 상한/하한 클램핑 (risk_max_per_trade_pct가 절대 상한)
    if adjusted_pct > max_per_trade_pct:
        adjustments.append(f"상한 적용 {adjusted_pct:.1f}% → {max_per_trade_pct}%")
        adjusted_pct = max_per_trade_pct
    if adjusted_pct < MIN_ALLOCATION_PCT:
        adjustments.append(f"하한 적용 {adjusted_pct:.1f}% → {MIN_ALLOCATION_PCT}%")
        adjusted_pct = MIN_ALLOCATION_PCT

    # 6) 금액 계산
    amount = krw_balance * (adjusted_pct / 100)

    return PositionSize(
        allocation_pct=round(adjusted_pct, 1),
        amount_krw=round(amount, 0),
        pattern_name=pattern_name,
        adjustments=adjustments,
    )


@dataclass(frozen=True)
class SellRecommendation:
    """매도 추천 결과."""

    recommended_pct: int    # 추천 매도 비율 (%)
    pattern_name: str       # 기준 패턴
    choices: list[int]      # 선택지 [20, 40, 60, 80, 100]


def calculate_sell_recommendation(
    patterns: list[dict[str, Any]] | None = None,
) -> SellRecommendation:
    """패턴 기반 매도 비율 추천 + 20% 단위 선택지."""
    choices = [20, 40, 60, 80, 100]
    pattern_name = "없음"
    recommended = SELL_DEFAULT_RECOMMENDATION

    if patterns:
        top = patterns[0]
        name = top.get("name", "")
        pattern_name = top.get("name_ko", name)
        recommended = SELL_PATTERN_RECOMMENDATION.get(name, SELL_DEFAULT_RECOMMENDATION)

    # 추천값을 20% 단위로 스냅
    recommended = max(20, min(100, round(recommended / 20) * 20))

    return SellRecommendation(
        recommended_pct=recommended,
        pattern_name=pattern_name,
        choices=choices,
    )

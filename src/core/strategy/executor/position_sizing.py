"""패턴 기반 포지션 사이징 — 패턴별 투자 비율 + 리스크 자동 조절."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 패턴별 기본 투자 비율 — DB(pattern_configs)에서 관리, 여기는 폴백
_FALLBACK_ALLOCATION: dict[str, float] = {
    "ascending_triangle": 20.0, "bull_flag": 30.0, "falling_wedge": 25.0,
    "double_bottom": 25.0, "triple_bottom": 30.0,
    "inverse_head_and_shoulders": 35.0,
    "double_top": 30.0, "triple_top": 35.0, "head_and_shoulders": 35.0,
    "descending_triangle": 20.0, "bear_flag": 30.0, "rising_wedge": 25.0,
}
_FALLBACK_SELL_REC: dict[str, int] = {
    "double_top": 100, "triple_top": 100, "head_and_shoulders": 80,
    "bear_flag": 80, "rising_wedge": 60, "descending_triangle": 60,
}

DEFAULT_ALLOCATION_PCT = 10.0
SELL_DEFAULT_RECOMMENDATION = 40

# DB에서 로드된 캐시 (pattern_service가 갱신)
_db_allocation: dict[str, float] | None = None
_db_sell_rec: dict[str, int] | None = None


def set_pattern_sizing(
    allocation: dict[str, float], sell_rec: dict[str, int],
) -> None:
    """DB에서 로드된 포지션 사이징으로 업데이트."""
    global _db_allocation, _db_sell_rec
    _db_allocation = allocation
    _db_sell_rec = sell_rec


def _get_allocation() -> dict[str, float]:
    return _db_allocation if _db_allocation is not None else _FALLBACK_ALLOCATION


def _get_sell_rec() -> dict[str, int]:
    return _db_sell_rec if _db_sell_rec is not None else _FALLBACK_SELL_REC


# 하위 호환용 (외부 참조)
PATTERN_ALLOCATION = _FALLBACK_ALLOCATION
SELL_PATTERN_RECOMMENDATION = _FALLBACK_SELL_REC

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
        raw_pct = _get_allocation().get(name, DEFAULT_ALLOCATION_PCT)
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
    *,
    confidence: float = 0.5,
) -> SellRecommendation:
    """확신도 + 패턴 기반 매도 비율 추천 + 20% 단위 선택지.

    확신도 기반 부분 매도 전략:
      conf >= 0.8 → 80%, >= 0.6 → 60%, >= 0.4 → 40%, 그 외 → 20%
    패턴이 전량 매도(100%)를 추천하면 확신도와 무관하게 상한 적용.
    """
    choices = [20, 40, 60, 80, 100]
    pattern_name = "없음"

    # 1) 확신도 기반 비율 (기본)
    if confidence >= 0.8:
        conf_pct = 80
    elif confidence >= 0.6:
        conf_pct = 60
    elif confidence >= 0.4:
        conf_pct = 40
    else:
        conf_pct = 20

    # 2) 패턴 기반 비율 (상한 보정)
    pattern_pct = SELL_DEFAULT_RECOMMENDATION
    if patterns:
        top = patterns[0]
        name = top.get("name", "")
        pattern_name = top.get("name_ko", name)
        pattern_pct = _get_sell_rec().get(name, SELL_DEFAULT_RECOMMENDATION)

    # 패턴이 전량(100%) 추천이면 확신도와 무관하게 전량 매도
    if pattern_pct >= 100:
        recommended = 100
    else:
        # 확신도 기반 비율을 기본으로, 패턴 추천이 더 높으면 상향
        recommended = max(conf_pct, pattern_pct)

    # 20% 단위 스냅
    recommended = max(20, min(100, round(recommended / 20) * 20))

    return SellRecommendation(
        recommended_pct=recommended,
        pattern_name=pattern_name,
        choices=choices,
    )

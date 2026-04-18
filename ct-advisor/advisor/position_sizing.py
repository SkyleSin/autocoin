"""포지션 사이징 — 패턴/지표/신뢰도 기반 투자 비율 결정."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ALLOCATION_PCT = 10.0
MIN_ALLOCATION_PCT = 3.0
SELL_DEFAULT_RECOMMENDATION = 40

# 패턴별 기본 매수 비율 폴백
_FALLBACK_ALLOCATION: dict[str, float] = {
    "ascending_triangle": 20.0, "bull_flag": 30.0, "falling_wedge": 25.0,
    "double_bottom": 25.0, "triple_bottom": 30.0,
    "inverse_head_and_shoulders": 35.0,
    "double_top": 30.0, "triple_top": 35.0, "head_and_shoulders": 35.0,
    "descending_triangle": 20.0, "bear_flag": 30.0, "rising_wedge": 25.0,
}

# 패턴별 매도 추천 비율 폴백
_FALLBACK_SELL_REC: dict[str, int] = {
    "double_top": 100, "triple_top": 100, "head_and_shoulders": 80,
    "bear_flag": 80, "rising_wedge": 60, "descending_triangle": 60,
}


def _get_indicator(indicators: dict[str, Any], key: str) -> float | None:
    """flat 또는 탑다운 layer 구조에서 지표값 추출."""
    val = indicators.get(key)
    if val is not None:
        return float(val)
    layer_15m = indicators.get("layer_15m", {})
    ind = layer_15m.get("indicators", {}) if isinstance(layer_15m, dict) else {}
    val = ind.get(key)
    return float(val) if val is not None else None


@dataclass(frozen=True)
class BuyRecommendation:
    """매수 포지션 사이징 결과 (비율만 — 금액은 ct-worker가 계산)."""
    allocation_pct: float
    pattern_name: str
    adjustments: list[str]


@dataclass(frozen=True)
class SellRecommendation:
    """매도 추천 결과."""
    recommended_pct: int
    pattern_name: str
    choices: list[int]


def calculate_buy_allocation(
    *,
    patterns: list[dict[str, Any]] | None = None,
    confidence: float = 0.5,
    indicators: dict[str, Any] | None = None,
    max_per_trade_pct: float = 15.0,
) -> BuyRecommendation:
    """매수 비율(%) 산출 — 실제 금액은 ct-worker가 잔고 기반으로 계산."""
    adjustments: list[str] = []

    # 1) 패턴/스코어링 기반 가중치
    pattern_name = "없음"
    pattern_weight = 0.5
    scoring = None

    if patterns:
        top = patterns[0]
        name = top.get("name", "")
        pattern_name = top.get("name_ko", name)
        raw_pct = _FALLBACK_ALLOCATION.get(name, DEFAULT_ALLOCATION_PCT)
        pattern_weight = max(0.3, min(raw_pct / 35.0, 1.0))
        adjustments.append(f"패턴 {pattern_name} 가중치 {pattern_weight:.0%}")
    elif indicators:
        scoring = indicators.get("scoring")
        if scoring:
            score_4h = scoring.get("score_4h", 0)
            multiplier = scoring.get("multiplier", 1.0)
            score_weight = min(score_4h / 6.0, 1.0)
            pattern_weight = max(0.5, min(score_weight * multiplier, 1.0))
            adjustments.append(
                f"탑다운 4H={score_4h:.2f} mult={multiplier:.2f} → {pattern_weight:.0%}"
            )

    base_pct = max_per_trade_pct * pattern_weight

    # 2) confidence 스케일링
    if scoring:
        conf_scale = max(0.7, min(confidence * 1.5, 1.0))
    else:
        conf_scale = max(0.5, min(confidence, 1.0))
    adjusted_pct = base_pct * conf_scale
    if conf_scale < 1.0:
        adjustments.append(f"confidence {confidence:.0%} → {conf_scale:.0%}")

    # 3) RSI 조절
    if indicators:
        rsi = _get_indicator(indicators, "rsi_14")
        if rsi is not None:
            if rsi < 20:
                adjusted_pct *= 1.2
                adjustments.append(f"RSI {rsi:.0f} 극과매도 +20%")
            elif rsi > 80:
                adjusted_pct *= 0.6
                adjustments.append(f"RSI {rsi:.0f} 극과매수 -40%")
            elif 60 < rsi < 70:
                adjusted_pct *= 0.8
                adjustments.append(f"RSI {rsi:.0f} 과매수 -20%")

    # 4) 볼린저밴드 수축
    if indicators:
        bb_upper = _get_indicator(indicators, "bb_upper")
        bb_lower = _get_indicator(indicators, "bb_lower")
        bb_middle = _get_indicator(indicators, "bb_middle")
        if bb_upper and bb_lower and bb_middle:
            bb_width = (bb_upper - bb_lower) / bb_middle * 100
            if bb_width < 1.0:
                adjusted_pct *= 0.7
                adjustments.append(f"볼린저 수축 ({bb_width:.1f}%) -30%")

    # 5) 클램핑
    if adjusted_pct > max_per_trade_pct:
        adjustments.append(f"상한 {adjusted_pct:.1f}% → {max_per_trade_pct}%")
        adjusted_pct = max_per_trade_pct
    if adjusted_pct < MIN_ALLOCATION_PCT:
        adjustments.append(f"하한 {adjusted_pct:.1f}% → {MIN_ALLOCATION_PCT}%")
        adjusted_pct = MIN_ALLOCATION_PCT

    return BuyRecommendation(
        allocation_pct=round(adjusted_pct, 1),
        pattern_name=pattern_name,
        adjustments=adjustments,
    )


def calculate_sell_recommendation(
    patterns: list[dict[str, Any]] | None = None,
    *,
    confidence: float = 0.5,
) -> SellRecommendation:
    """매도 비율(%) 추천 — 확신도 + 패턴 기반."""
    choices = [20, 40, 60, 80, 100]
    pattern_name = "없음"

    if confidence >= 0.8:
        conf_pct = 80
    elif confidence >= 0.6:
        conf_pct = 60
    elif confidence >= 0.4:
        conf_pct = 40
    else:
        conf_pct = 20

    pattern_pct = SELL_DEFAULT_RECOMMENDATION
    if patterns:
        top = patterns[0]
        name = top.get("name", "")
        pattern_name = top.get("name_ko", name)
        pattern_pct = _FALLBACK_SELL_REC.get(name, SELL_DEFAULT_RECOMMENDATION)

    if pattern_pct >= 100:
        recommended = 100
    else:
        recommended = max(conf_pct, pattern_pct)

    recommended = max(20, min(100, round(recommended / 20) * 20))

    return SellRecommendation(
        recommended_pct=recommended,
        pattern_name=pattern_name,
        choices=choices,
    )

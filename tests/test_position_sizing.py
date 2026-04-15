"""테스트: 포지션 사이징 risk_max_per_trade_pct 적용."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.strategy.executor.position_sizing import (
    calculate_position,
    calculate_sell_recommendation,
)


def test_max_per_trade_pct_cap():
    """어떤 패턴/confidence 조합이든 max_per_trade_pct를 초과하지 않아야 함."""
    # 가장 강한 조건: 역머리어깨(35%) + confidence 1.0 + RSI 극과매도
    pos = calculate_position(
        1_000_000,
        patterns=[{"name": "inverse_head_and_shoulders", "name_ko": "역머리어깨"}],
        confidence=1.0,
        indicators={"rsi_14": 15},
        max_per_trade_pct=15.0,
    )
    assert pos.allocation_pct <= 15.0
    assert pos.amount_krw <= 150_000


def test_default_max_15():
    """기본 max_per_trade_pct=15 적용."""
    pos = calculate_position(
        1_000_000,
        patterns=[{"name": "bull_flag", "name_ko": "상승깃발"}],
        confidence=0.9,
    )
    assert pos.allocation_pct <= 15.0


def test_no_pattern_lower_allocation():
    """패턴 없으면 가중치 0.5 → max의 절반 이하."""
    pos = calculate_position(
        1_000_000,
        confidence=0.5,
        max_per_trade_pct=15.0,
    )
    # 패턴 없음(0.5) × confidence(0.5) = 15 × 0.5 × 0.5 = 3.75%
    assert pos.allocation_pct <= 7.5
    assert pos.amount_krw <= 75_000


def test_min_allocation():
    """최소 비율(3%) 미만으로 떨어지지 않아야 함."""
    pos = calculate_position(
        1_000_000,
        confidence=0.5,
        indicators={"rsi_14": 85, "bb_upper": 100, "bb_lower": 99.5, "bb_middle": 100},
        max_per_trade_pct=5.0,
    )
    assert pos.allocation_pct >= 3.0


def test_custom_max_pct():
    """사용자 설정 max_per_trade_pct 반영."""
    pos = calculate_position(
        1_000_000,
        patterns=[{"name": "double_top", "name_ko": "쌍봉"}],
        confidence=1.0,
        max_per_trade_pct=10.0,
    )
    assert pos.allocation_pct <= 10.0
    assert pos.amount_krw <= 100_000


def test_rsi_extreme_oversold_boost():
    """RSI < 20 → 20% 상향이지만 max를 넘지 않음."""
    pos = calculate_position(
        1_000_000,
        patterns=[{"name": "falling_wedge", "name_ko": "하락쐐기형"}],
        confidence=1.0,
        indicators={"rsi_14": 18},
        max_per_trade_pct=15.0,
    )
    assert pos.allocation_pct <= 15.0


def test_sell_recommendation_double_top():
    """쌍봉 → 100% 매도 추천."""
    rec = calculate_sell_recommendation(
        [{"name": "double_top", "name_ko": "쌍봉"}],
    )
    assert rec.recommended_pct == 100


def test_sell_recommendation_no_pattern():
    """패턴 없음 → 40% 기본 추천."""
    rec = calculate_sell_recommendation(None)
    assert rec.recommended_pct == 40
    assert rec.choices == [20, 40, 60, 80, 100]

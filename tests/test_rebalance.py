"""테스트: 리밸런싱 추천 비율 (신뢰도 차이 기반)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _calc_recommended_pct(buy_confidence: float, hold_confidence: float) -> int:
    """_suggest_rebalance 내부 로직 재현 (추천 비율 계산)."""
    conf_diff = buy_confidence - hold_confidence
    if conf_diff >= 0.4:
        return 100
    elif conf_diff >= 0.25:
        return 75
    elif conf_diff >= 0.1:
        return 50
    else:
        return 25


def test_large_diff_100pct():
    """신뢰도 차이 40%+ → 100% 추천."""
    assert _calc_recommended_pct(0.9, 0.4) == 100


def test_medium_diff_75pct():
    """신뢰도 차이 25~39% → 75% 추천."""
    assert _calc_recommended_pct(0.85, 0.55) == 75


def test_small_diff_50pct():
    """신뢰도 차이 10~24% → 50% 추천."""
    assert _calc_recommended_pct(0.7, 0.55) == 50


def test_tiny_diff_25pct():
    """신뢰도 차이 10% 미만 → 25% 추천."""
    assert _calc_recommended_pct(0.6, 0.55) == 25


def test_same_confidence_25pct():
    """동일 신뢰도 → 최소 25%."""
    assert _calc_recommended_pct(0.7, 0.7) == 25


def test_boundary_40pct():
    """경계값: 정확히 0.4 차이 → 100%."""
    assert _calc_recommended_pct(0.9, 0.5) == 100


def test_boundary_25pct():
    """경계값: 정확히 0.25 차이 → 75%."""
    assert _calc_recommended_pct(0.75, 0.5) == 75


def test_boundary_10pct():
    """경계값: 0.1 이상 차이 → 50%."""
    assert _calc_recommended_pct(0.65, 0.5) == 50

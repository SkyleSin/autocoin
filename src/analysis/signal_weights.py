"""적중률 기반 지표 가중치 + 베이지안 결합.

타임프레임별 백테스트 적중률을 base로, DB에서 적응형 갱신.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Weight:
    """지표 가중치."""

    name: str
    direction: str  # "BUY" | "SELL"
    accuracy: float  # 0.0 ~ 1.0
    is_active: bool  # 50% 미만이면 False


# ── 백테스트 기반 초기 가중치 (1년, 4코인) ──

# 5분봉 (2h 후 평가, 30,543건)
BASE_WEIGHTS_5M: dict[str, Weight] = {
    # SELL 유효
    "rsi_overbought": Weight("rsi_overbought", "SELL", 0.557, True),
    "bb_upper": Weight("bb_upper", "SELL", 0.533, True),
    # BUY 유효
    "rsi_oversold": Weight("rsi_oversold", "BUY", 0.553, True),
    "bb_lower": Weight("bb_lower", "BUY", 0.536, True),
    # 무효 (50% 미만)
    "macd_dead": Weight("macd_dead", "SELL", 0.484, False),
    "macd_golden": Weight("macd_golden", "BUY", 0.473, False),
    # S/R (공간 부스터로만 사용, 독립 시그널 아님)
    "sr_support_bounce": Weight("sr_support_bounce", "BUY", 0.490, False),
    "sr_resistance_reject": Weight("sr_resistance_reject", "SELL", 0.487, False),
    "sr_support_break": Weight("sr_support_break", "SELL", 0.483, False),
    "sr_resistance_break": Weight("sr_resistance_break", "BUY", 0.464, False),
}

# 15분봉 (8h 후 평가, 6,059건)
BASE_WEIGHTS_15M: dict[str, Weight] = {
    "bb_upper": Weight("bb_upper", "SELL", 0.593, True),
    "bb_lower": Weight("bb_lower", "BUY", 0.545, True),
    "rsi_overbought": Weight("rsi_overbought", "SELL", 0.535, True),
    "macd_dead": Weight("macd_dead", "SELL", 0.519, True),
    "sr_resistance_reject": Weight("sr_resistance_reject", "SELL", 0.503, True),
    # 무효
    "macd_golden": Weight("macd_golden", "BUY", 0.503, False),  # 경계선
    "sr_support_break": Weight("sr_support_break", "SELL", 0.495, False),
    "sr_resistance_break": Weight("sr_resistance_break", "BUY", 0.488, False),
    "sr_support_bounce": Weight("sr_support_bounce", "BUY", 0.472, False),
    "rsi_oversold": Weight("rsi_oversold", "BUY", 0.411, False),
}

# 1시간봉 (24h 후 평가, 3,403건)
BASE_WEIGHTS_1H: dict[str, Weight] = {
    "macd_dead": Weight("macd_dead", "SELL", 0.599, True),
    "rsi_overbought": Weight("rsi_overbought", "SELL", 0.547, True),
    "sr_support_break": Weight("sr_support_break", "SELL", 0.525, True),
    "sr_resistance_reject": Weight("sr_resistance_reject", "SELL", 0.508, True),
    "bb_upper": Weight("bb_upper", "SELL", 0.506, True),
    # 무효
    "sr_resistance_break": Weight("sr_resistance_break", "BUY", 0.498, False),
    "sr_support_bounce": Weight("sr_support_bounce", "BUY", 0.480, False),
    "bb_lower": Weight("bb_lower", "BUY", 0.465, False),
    "macd_golden": Weight("macd_golden", "BUY", 0.442, False),
    "rsi_oversold": Weight("rsi_oversold", "BUY", 0.400, False),
}

# 타임프레임 → 가중치 매핑
_BASE_WEIGHTS: dict[str, dict[str, Weight]] = {
    "5m": BASE_WEIGHTS_5M,
    "15m": BASE_WEIGHTS_15M,
    "1h": BASE_WEIGHTS_1H,
}

# DB 캐시 (서버 기동 시 또는 갱신 시 로드)
_db_weights: dict[str, dict[str, Weight]] | None = None


def get_weights(timeframe: str) -> dict[str, Weight]:
    """타임프레임별 가중치 반환. DB 캐시 우선, 없으면 base."""
    if _db_weights and timeframe in _db_weights:
        return _db_weights[timeframe]
    return _BASE_WEIGHTS.get(timeframe, BASE_WEIGHTS_5M)


def set_weights_cache(timeframe: str, weights: dict[str, Weight]) -> None:
    """DB에서 로드한 가중치를 캐시에 저장."""
    global _db_weights  # noqa: PLW0603
    if _db_weights is None:
        _db_weights = {}
    _db_weights[timeframe] = weights


def bayesian_combine(accuracies: list[float], prior: float = 0.5) -> float:
    """독립 지표의 적중률을 베이지안 결합하여 최종 확률 산출.

    Naive Bayes log-odds 방식:
    - 적중률 50% → 기여도 0 (동전)
    - 적중률 55% → 약한 양의 기여
    - 적중률 60% → 강한 양의 기여
    - 적중률 45% → 역방향 기여 (자동 패널티)
    """
    if not accuracies:
        return prior

    log_odds = math.log(prior / (1 - prior))
    for acc in accuracies:
        acc = max(0.01, min(0.99, acc))  # 극단값 클리핑
        log_odds += math.log(acc / (1 - acc))

    combined = 1 / (1 + math.exp(-log_odds))
    return round(combined, 4)

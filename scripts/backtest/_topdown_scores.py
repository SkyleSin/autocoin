"""탑다운 MTF 스코어링 — 3단계 구조.

  1단계: 4H 방향 게이트
  2단계: 1H 신뢰도 부스트 (multiplier)
  3단계: 15m 타이밍 트리거 (yes/no)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from _topdown_scores_tf import (
    MAX_BUY_1H,
    MAX_BUY_4H,
    MAX_SELL_1H,
    MAX_SELL_4H,
    score_1h,
    score_4h,
    trigger_15m,
)

# ── 4H 방향 게이트 파라미터 ──
GAP_4H = 0.5
THRESHOLD_BUY = 2.0
THRESHOLD_SELL = 1.5

# ── 1H 부스트 파라미터 ──
BOOST_WEIGHT = 0.4
COUNTER_WEIGHT = 0.2
MULT_MIN = 0.6
MULT_MAX = 1.5


@dataclass
class TopdownSignal:
    direction: str              # BUY | SELL | HOLD
    buy_score: float            # 4H BUY 점수
    sell_score: float           # 4H SELL 점수
    multiplier: float           # 1H 부스트 multiplier
    confidence: float           # 최종 confidence
    fired_indicators: list[str] = field(default_factory=list)
    hold_reason: str = ""


def _gate_4h(buy_4h: float, sell_4h: float, gap: float) -> str:
    if buy_4h - sell_4h >= gap and buy_4h >= THRESHOLD_BUY:
        return "BUY"
    if sell_4h - buy_4h >= gap and sell_4h >= THRESHOLD_SELL:
        return "SELL"
    return "HOLD"


def _calc_multiplier(direction: str, buy_1h: float, sell_1h: float) -> float:
    if direction == "BUY":
        boost = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0.0
        counter = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0.0
    else:
        boost = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0.0
        counter = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0.0
    raw = 1.0 + boost * BOOST_WEIGHT - counter * COUNTER_WEIGHT
    return max(MULT_MIN, min(MULT_MAX, raw))


def compute_signal(
    snap_4h, sr_4h, close_4h: float,
    snap_1h, sr_1h, close_1h: float,
    snap_15m, close_15m: float,
    gap: float = GAP_4H,
) -> TopdownSignal:
    """3단계 탑다운 시그널 산출."""
    fired: list[str] = []

    # ── 1단계: 4H 방향 게이트 ──
    buy_4h, sell_4h, fired = score_4h(snap_4h, sr_4h, close_4h, 0.0, 0.0, fired)
    direction = _gate_4h(buy_4h, sell_4h, gap)
    if direction == "HOLD":
        return TopdownSignal(
            "HOLD", round(buy_4h, 2), round(sell_4h, 2), 1.0, 0.0,
            fired, f"4H gap 부족 (B={buy_4h:.2f} S={sell_4h:.2f})",
        )

    dir_score = buy_4h if direction == "BUY" else sell_4h

    # ── 2단계: 1H 신뢰도 부스트 ──
    buy_1h, sell_1h = 0.0, 0.0
    if snap_1h is not None:
        buy_1h, sell_1h, fired = score_1h(snap_1h, sr_1h, close_1h, buy_1h, sell_1h, fired)
    multiplier = _calc_multiplier(direction, buy_1h, sell_1h)

    # ── 3단계: 15m 타이밍 트리거 ──
    if snap_15m is not None:
        triggered, trig_fired = trigger_15m(snap_15m, close_15m, direction)
        fired.extend(trig_fired)
    else:
        triggered = False

    if not triggered:
        return TopdownSignal(
            "HOLD", round(buy_4h, 2), round(sell_4h, 2), multiplier, 0.0,
            fired, "15m 트리거 미충족",
        )

    # ── confidence 계산 ──
    max_4h = MAX_BUY_4H if direction == "BUY" else MAX_SELL_4H
    conf = min(dir_score / max_4h, 1.0) * multiplier if max_4h > 0 else 0.0
    conf = min(conf, 1.0)

    return TopdownSignal(
        direction, round(buy_4h, 2), round(sell_4h, 2),
        round(multiplier, 3), round(conf, 4), fired,
    )

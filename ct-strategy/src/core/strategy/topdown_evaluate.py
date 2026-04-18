"""탑다운 MTF 전략 — 3단계 평가 헬퍼.

평가 구조:
  1단계: 4H 방향 게이트 -- buy/sell 점수 차이(gap)와 임계값으로 방향 결정
  2단계: 1H 신뢰도 부스트 -- 4H 방향과 같은 방향 1H 지표로 multiplier 산출
  3단계: 15m 타이밍 트리거 -- 진입 조건(yes/no)이 충족될 때만 시그널 발생
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from infra.api.indicator_snapshot import get_latest_snapshot

from .snapshot_helpers import rebuild_snap, rebuild_sr
from .topdown_layer import evaluate_layer, trigger_check
from .topdown_tables import MAX_BUY_1H, MAX_BUY_4H, MAX_SELL_1H, MAX_SELL_4H

if TYPE_CHECKING:
    from .base import StrategyResult

logger = logging.getLogger(__name__)

# -- 4H 방향 게이트 파라미터 --
_GAP_4H = 0.5
_THRESHOLD_BUY = 2.5
_THRESHOLD_SELL = 1.8

# -- 1H 부스트 파라미터 --
_BOOST_WEIGHT = 0.4
_COUNTER_WEIGHT = 0.2
_MULT_MIN = 0.6
_MULT_MAX = 1.5


async def eval_single_tf(
    market: str, tf: str, buy_ind: list, sell_ind: list,
) -> tuple[float, float, list[str], dict, dict] | None:
    """단일 TF 평가. 데이터 없으면 None."""
    data = await get_latest_snapshot(market, tf)
    if not data:
        return None
    snap = rebuild_snap(data)
    if not snap:
        return None
    close = float(data.get("close_price", 0))
    sr = rebuild_sr(data.get("support_resistance"))
    buy_s, sell_s, reasons, detail = evaluate_layer(
        snap, close, sr, buy_ind, sell_ind,
    )
    extra = {
        "close_price": close, "indicators": snap.to_dict(),
        "support_resistance": data.get("support_resistance"),
    }
    return buy_s, sell_s, reasons, detail, extra


async def eval_trigger_15m(
    market: str, trigger_fns: list,
) -> tuple[bool, list[str], dict] | None:
    """15m 타이밍 트리거 체크. 데이터 없으면 None."""
    data = await get_latest_snapshot(market, "15m")
    if not data:
        return None
    snap = rebuild_snap(data)
    if not snap:
        return None
    close = float(data.get("close_price", 0))
    triggered, fired = trigger_check(snap, close, trigger_fns)
    extra = {"close_price": close, "indicators": snap.to_dict()}
    return triggered, fired, extra


def calc_1h_multiplier(
    direction: str, buy_1h: float, sell_1h: float,
) -> tuple[float, str]:
    """1H 점수 -> multiplier 산출."""
    if direction == "BUY":
        boost = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0.0
        counter = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0.0
    else:
        boost = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0.0
        counter = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0.0

    raw = 1.0 + boost * _BOOST_WEIGHT - counter * _COUNTER_WEIGHT
    mult = max(_MULT_MIN, min(_MULT_MAX, raw))
    reason = (
        f"1H mult={mult:.2f}"
        f" (boost={boost:.2f}x{_BOOST_WEIGHT}"
        f" counter={counter:.2f}x{_COUNTER_WEIGHT})"
    )
    return mult, reason


async def decide_topdown(
    direction: str, score_4h: float, multiplier: float,
    reasons: list[str], layers: dict[str, Any],
) -> StrategyResult:
    """4H 점수 x 1H multiplier -> 최종 confidence 산출."""
    from .base import StrategyResult
    from .strategy_5m_trigger import _check_bear_market

    max_4h = MAX_BUY_4H if direction == "BUY" else MAX_SELL_4H
    conf = min(score_4h / max_4h, 1.0) * multiplier if max_4h > 0 else 0.0
    conf = min(conf, 1.0)

    reasons.append(
        f"{direction} 4H={score_4h:.2f} xmult={multiplier:.2f} conf={conf:.2%}"
    )
    layers["scoring"] = {
        "direction": direction, "score_4h": round(score_4h, 2),
        "multiplier": round(multiplier, 2), "confidence": round(conf, 4),
    }
    meta: dict[str, Any] = {"status": "ok", "indicators": layers}
    meta["bear_market"] = await _check_bear_market()

    logger.info(
        "topdown_mtf: %s (4h=%.2f mult=%.2f conf=%.2f)",
        direction, score_4h, multiplier, conf,
    )
    return StrategyResult(direction, round(conf, 4), reasons, meta)


def gate_4h(
    buy_4h: float, sell_4h: float, gap: float = _GAP_4H,
) -> str:
    """4H 방향 게이트. Returns 'BUY' | 'SELL' | 'HOLD'."""
    if buy_4h - sell_4h >= gap and buy_4h >= _THRESHOLD_BUY:
        return "BUY"
    if sell_4h - buy_4h >= gap and sell_4h >= _THRESHOLD_SELL:
        return "SELL"
    return "HOLD"


def _make_hold(
    status: str, layers: dict[str, Any],
    reasons: list[str] | None = None,
) -> StrategyResult:
    from .base import StrategyResult
    return StrategyResult(
        signal_type="HOLD", confidence=0.0,
        reasons=reasons or [status],
        metadata={"status": status, "indicators": layers},
    )

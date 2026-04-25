"""탑다운 MTF 전략 — 3단계 평가 헬퍼."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from infra.api.indicator_snapshot import get_latest_snapshot

from .snapshot_helpers import rebuild_snap, rebuild_sr
from .topdown_layer import evaluate_layer, trigger_check, trigger_check_v2
from .topdown_tables import MAX_BUY_1H, MAX_SELL_1H
from .types import Snap as IndicatorSnap

if TYPE_CHECKING:
    from .base import StrategyResult

logger = logging.getLogger(__name__)

# ── 4H 방향 게이트 파라미터 (2026-04-20 BTC+ETH 백테스트 비례 조정) ──
_GAP_4H = 1.52
_THRESHOLD_BUY = 7.57
_THRESHOLD_SELL = 6.0

# ── 1H 부스트 파라미터 ──
_BOOST_WEIGHT = 0.4
_COUNTER_WEIGHT = 0.2
_MULT_MIN = 0.6
_MULT_MAX = 1.5


async def eval_single_tf(
    market: str, tf: str, buy_ind: list, sell_ind: list,
) -> tuple[float, float, list[str], dict, dict, IndicatorSnap] | None:
    """단일 TF 평가. 데이터 없으면 None."""
    data = await get_latest_snapshot(market, tf)
    if not data:
        return None
    snap = rebuild_snap(data)
    if not snap:
        return None
    close = float(data.get("close_price", 0))
    sr = rebuild_sr(data.get("support_resistance"))
    buy_s, sell_s, reasons, detail = evaluate_layer(snap, close, sr, buy_ind, sell_ind)
    extra = {
        "close_price": close,
        "indicators": snap.to_dict(),
        "support_resistance": data.get("support_resistance"),
    }
    return buy_s, sell_s, reasons, detail, extra, snap


async def eval_trigger_15m(
    market: str,
    trigger_fns: list | None = None,
    *,
    required_fns: list | None = None,
    optional_fns: list | None = None,
) -> tuple[bool, list[str], dict] | None:
    """15m 타이밍 트리거 체크. 데이터 없으면 None."""
    data = await get_latest_snapshot(market, "15m")
    if not data:
        return None
    snap = rebuild_snap(data)
    if not snap:
        return None
    close = float(data.get("close_price", 0))
    if required_fns is not None and optional_fns is not None:
        triggered, fired = trigger_check_v2(snap, close, required_fns, optional_fns)
    else:
        triggered, fired = trigger_check(snap, close, trigger_fns or [])
    sr = rebuild_sr(data.get("support_resistance"))
    extra = {
        "close_price": close,
        "indicators": snap.to_dict(),
        "support_resistance": data.get("support_resistance"),
        "sr_signal": sr.signal if sr else "NONE",
    }
    return triggered, fired, extra


def calc_1h_multiplier(
    direction: str, buy_1h: float, sell_1h: float,
) -> tuple[float, str]:
    """1H 점수 → multiplier 산출."""
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
    """충족 지표 가중평균 적중률 × 1H multiplier → 최종 confidence 산출.

    score = (accuracy - 50) / 10 이므로,
    avg_accuracy = (sum_score / hit_count) * 10 + 50
    """
    from .base import StrategyResult

    layer_4h = layers.get("layer_4h", {})
    hits_key = "buy_hits" if direction == "BUY" else "sell_hits"
    hit_count = len(layer_4h.get(hits_key, []))

    if hit_count > 0:
        avg_score = score_4h / hit_count
        avg_accuracy = avg_score * 10 + 50  # 원래 적중률(%) 복원
        conf = (avg_accuracy / 100) * multiplier
    else:
        conf = 0.0
    conf = min(conf, 1.0)

    reasons.append(
        f"{direction} 4H={score_4h:.2f} hits={hit_count} xmult={multiplier:.2f} conf={conf:.2%}"
    )
    layers["scoring"] = {
        "direction": direction,
        "score_4h": round(score_4h, 2),
        "hit_count": hit_count,
        "multiplier": round(multiplier, 2),
        "confidence": round(conf, 4),
    }
    meta: dict[str, Any] = {"status": "ok", "indicators": layers}

    logger.info(
        "topdown_mtf: %s (4h=%.2f hits=%d mult=%.2f conf=%.2f)",
        direction, score_4h, hit_count, multiplier, conf,
    )
    return StrategyResult(direction, round(conf, 4), reasons, meta)


def gate_4h(
    buy_4h: float,
    sell_4h: float,
    snap_4h: IndicatorSnap | None = None,
    gap: float = _GAP_4H,
) -> str:
    """4H 방향 게이트. Returns 'BUY' | 'SELL' | 'HOLD'.

    MACD 히스토그램 하드필터:
    - 강한 음수(< -50K) → BUY 차단
    - 강한 양수(> +50K) → SELL 차단
    """
    _MACD_BLOCK = 50_000

    if snap_4h and snap_4h.macd_histogram is not None:
        macd_h = snap_4h.macd_histogram
        if macd_h < -_MACD_BLOCK:
            # 강한 하락 추세 — BUY 불허, SELL만 허용
            if sell_4h - buy_4h >= gap and sell_4h >= _THRESHOLD_SELL:
                return "SELL"
            return "HOLD"
        if macd_h > _MACD_BLOCK:
            # 강한 상승 추세 — SELL 불허, BUY만 허용
            if buy_4h - sell_4h >= gap and buy_4h >= _THRESHOLD_BUY:
                return "BUY"
            return "HOLD"

    if buy_4h - sell_4h >= gap and buy_4h >= _THRESHOLD_BUY:
        return "BUY"
    if sell_4h - buy_4h >= gap and sell_4h >= _THRESHOLD_SELL:
        return "SELL"
    return "HOLD"


def _make_hold(
    status: str, layers: dict[str, Any], reasons: list[str] | None = None,
) -> StrategyResult:
    from .base import StrategyResult
    return StrategyResult(
        signal_type="HOLD",
        confidence=0.0,
        reasons=reasons or [status],
        metadata={"status": status, "indicators": layers},
    )

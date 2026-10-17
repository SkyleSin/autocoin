"""탑다운 MTF 전략 — 3단계 구조.

  1단계: 4H 방향 게이트 (필수)
  2단계: 1H 신뢰도 부스트
  3단계: 15m 타이밍 트리거 (yes/no 게이트)

confidence = avg_accuracy(충족 지표) × 1H_multiplier
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseStrategy, StrategyResult, register_strategy
from .topdown_evaluate import (
    _make_hold,
    calc_1h_multiplier,
    decide_topdown,
    eval_single_tf,
    eval_trigger_15m,
    gate_4h,
)
from .topdown_tables import (
    BUY_1H,
    BUY_4H,
    SELL_1H,
    SELL_4H,
    TRIGGER_BUY_15M_OPTIONAL,
    TRIGGER_BUY_15M_REQUIRED,
    TRIGGER_SELL_15M_OPTIONAL,
    TRIGGER_SELL_15M_REQUIRED,
)

if TYPE_CHECKING:
    from utils.config import Settings


@register_strategy
class StrategyTopdownMTF(BaseStrategy):
    name = "topdown_mtf"
    description = "4H 방향 게이트 -> 1H 부스트 -> 15m 트리거 탑다운 전략"

    def required_timeframes(self) -> list[str]:
        return ["15m", "1h", "4h"]

    async def evaluate(self, market: str, settings: Settings) -> StrategyResult:
        """3단계 탑다운 평가."""
        layers: dict[str, Any] = {}
        all_reasons: list[str] = []

        # 1단계: 4H 방향 게이트
        r4h = await eval_single_tf(market, "4h", BUY_4H, SELL_4H)
        if r4h is None:
            return _make_hold("4H 데이터 없음", layers)
        buy_4h, sell_4h, reasons_4h, detail_4h, extra_4h, snap_4h = r4h
        layers["layer_4h"] = {**detail_4h, **extra_4h}
        all_reasons.extend(f"[4H] {r}" for r in reasons_4h)

        direction = gate_4h(buy_4h, sell_4h, snap_4h=snap_4h)
        if direction == "HOLD":
            all_reasons.append(f"4H 방향 미결정 (BUY={buy_4h:.2f} SELL={sell_4h:.2f})")
            return _make_hold("4H 방향 미결정", layers, all_reasons)

        dir_score_4h = buy_4h if direction == "BUY" else sell_4h
        all_reasons.append(f"[4H] 방향={direction} score={dir_score_4h:.2f}")

        # 2단계: 1H 신뢰도 부스트
        r1h = await eval_single_tf(market, "1h", BUY_1H, SELL_1H)
        if r1h is not None:
            buy_1h, sell_1h, reasons_1h, detail_1h, extra_1h, _ = r1h
            layers["layer_1h"] = {**detail_1h, **extra_1h}
            all_reasons.extend(f"[1H] {r}" for r in reasons_1h)
            multiplier, mult_reason = calc_1h_multiplier(direction, buy_1h, sell_1h)
            all_reasons.append(f"[1H] {mult_reason}")
        else:
            multiplier = 1.0
            all_reasons.append("[1H] 데이터 없음 -> multiplier=1.0")

        # 3단계: 15m 타이밍 트리거 (required + optional)
        if direction == "BUY":
            req_fns, opt_fns = TRIGGER_BUY_15M_REQUIRED, TRIGGER_BUY_15M_OPTIONAL
        else:
            req_fns, opt_fns = TRIGGER_SELL_15M_REQUIRED, TRIGGER_SELL_15M_OPTIONAL
        r15m = await eval_trigger_15m(
            market, required_fns=req_fns, optional_fns=opt_fns,
        )
        if r15m is None:
            return _make_hold("15m 데이터 없음", layers, all_reasons)

        triggered, fired_15m, extra_15m = r15m
        layers["layer_15m"] = {"triggered": triggered, "fired": fired_15m, **extra_15m}

        if not triggered:
            all_reasons.append(f"[15m] {direction} 트리거 미충족 (타이밍 대기)")
            return _make_hold("15m 트리거 미충족", layers, all_reasons)

        all_reasons.append(f"[15m] 트리거 충족: {', '.join(fired_15m)}")

        # 거래량 필터 — 15m 거래량 비율이 최소 기준 미달 시 HOLD
        _VOL_MIN_RATIO = 0.2
        vol_ratio = (extra_15m.get("indicators") or {}).get("volume_ratio")
        if vol_ratio is not None and vol_ratio < _VOL_MIN_RATIO:
            all_reasons.append(
                f"[15m] 거래량 부족 (vol_ratio={vol_ratio:.2f} < {_VOL_MIN_RATIO})"
            )
            return _make_hold("15m 거래량 부족", layers, all_reasons)

        # SR 방향 충돌 체크 — 지지 이탈+매수, 저항 거부+매수 등 논리적 모순 차단
        _SR_CONFLICT = {
            "BUY": {"SUPPORT_BREAK", "RESISTANCE_REJECT"},
            "SELL": {"RESISTANCE_BREAK", "SUPPORT_BOUNCE"},
        }
        sr_signal = extra_15m.get("sr_signal", "NONE")
        if sr_signal in _SR_CONFLICT.get(direction, set()):
            all_reasons.append(f"[15m] SR 방향 충돌: {direction} vs {sr_signal}")
            return _make_hold("SR 방향 충돌", layers, all_reasons)

        # SR=NONE 시 multiplier 감쇠 — SR 근거 없는 시그널의 confidence 하향
        if sr_signal == "NONE":
            multiplier *= 0.95
            all_reasons.append("[15m] SR 시그널 없음 → multiplier 5% 감쇠")

        return await decide_topdown(direction, dir_score_4h, multiplier, all_reasons, layers)

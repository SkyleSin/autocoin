"""탑다운 MTF 전략 — 3단계 구조.

  1단계: 4H 방향 게이트 (필수)
         BUY_4H - SELL_4H >= gap AND BUY_4H >= threshold → 방향 결정
  2단계: 1H 신뢰도 부스트
         같은 방향 1H 지표 → multiplier (0.6 ~ 1.5)
  3단계: 15m 타이밍 트리거 (yes/no 게이트)
         RSI/BB 진입 조건 충족 시에만 시그널 발생

confidence = (4H_score / MAX_4H) x 1H_multiplier
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
    TRIGGER_BUY_15M,
    TRIGGER_SELL_15M,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from utils.config import Settings


@register_strategy
class StrategyTopdownMTF(BaseStrategy):
    name = "topdown_mtf"
    description = "4H 방향 게이트 → 1H 부스트 → 15m 트리거 탑다운 전략"

    def required_timeframes(self) -> list[str]:
        return ["15m", "1h", "4h"]

    async def evaluate(
        self,
        session: AsyncSession,
        market: str,
        settings: Settings,
    ) -> StrategyResult:
        """3단계 탑다운 평가."""
        layers: dict[str, Any] = {}
        all_reasons: list[str] = []

        # ── 1단계: 4H 방향 게이트 ──
        r4h = await eval_single_tf(session, market, "4h", BUY_4H, SELL_4H)
        if r4h is None:
            return _make_hold("4H 데이터 없음", layers)
        buy_4h, sell_4h, reasons_4h, detail_4h, extra_4h = r4h
        layers["layer_4h"] = {**detail_4h, **extra_4h}
        all_reasons.extend(f"[4H] {r}" for r in reasons_4h)

        direction = gate_4h(buy_4h, sell_4h)
        if direction == "HOLD":
            all_reasons.append(
                f"4H 방향 미결정 (BUY={buy_4h:.2f} SELL={sell_4h:.2f})"
            )
            return _make_hold("4H 방향 미결정", layers, all_reasons)

        dir_score_4h = buy_4h if direction == "BUY" else sell_4h
        all_reasons.append(f"[4H] 방향={direction} score={dir_score_4h:.2f}")

        # ── 2단계: 1H 신뢰도 부스트 ──
        r1h = await eval_single_tf(session, market, "1h", BUY_1H, SELL_1H)
        if r1h is not None:
            buy_1h, sell_1h, reasons_1h, detail_1h, extra_1h = r1h
            layers["layer_1h"] = {**detail_1h, **extra_1h}
            all_reasons.extend(f"[1H] {r}" for r in reasons_1h)
            multiplier, mult_reason = calc_1h_multiplier(direction, buy_1h, sell_1h)
            all_reasons.append(f"[1H] {mult_reason}")
        else:
            multiplier = 1.0
            all_reasons.append("[1H] 데이터 없음 → multiplier=1.0")

        # ── 3단계: 15m 타이밍 트리거 ──
        trigger_fns = TRIGGER_BUY_15M if direction == "BUY" else TRIGGER_SELL_15M
        r15m = await eval_trigger_15m(session, market, trigger_fns)
        if r15m is None:
            return _make_hold("15m 데이터 없음", layers, all_reasons)

        triggered, fired_15m, extra_15m = r15m
        layers["layer_15m"] = {"triggered": triggered, "fired": fired_15m, **extra_15m}

        if not triggered:
            all_reasons.append(f"[15m] {direction} 트리거 미충족 (타이밍 대기)")
            return _make_hold("15m 트리거 미충족", layers, all_reasons)

        all_reasons.append(f"[15m] 트리거 충족: {', '.join(fired_15m)}")

        return await decide_topdown(
            session, direction, dir_score_4h, multiplier, all_reasons, layers,
        )

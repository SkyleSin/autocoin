"""탑다운 MTF 전략 — 레이어 평가 함수."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.analysis.indicators import IndicatorSnapshot as IndicatorSnap
    from core.analysis.support_resistance import SRAnalysis


def evaluate_layer(
    snap: IndicatorSnap,
    close_price: float,
    sr: SRAnalysis | None,
    indicators_buy: list[tuple[str, float, Callable]],
    indicators_sell: list[tuple[str, float, Callable]],
) -> tuple[float, float, list[str], dict[str, Any]]:
    """지표 리스트를 순회하며 BUY/SELL 점수를 합산한다.

    Returns:
        (buy_score, sell_score, reasons, detail)
    """
    buy_score = 0.0
    sell_score = 0.0
    reasons: list[str] = []
    buy_hits: list[str] = []
    sell_hits: list[str] = []

    for name, score, check_fn in indicators_buy:
        if check_fn(snap, close_price, sr):
            buy_score += score
            buy_hits.append(f"{name}(+{score:.2f})")
            reasons.append(f"BUY {name} +{score:.2f}")

    for name, score, check_fn in indicators_sell:
        if check_fn(snap, close_price, sr):
            sell_score += score
            sell_hits.append(f"{name}(+{score:.2f})")
            reasons.append(f"SELL {name} +{score:.2f}")

    detail = {
        "buy_hits": buy_hits,
        "sell_hits": sell_hits,
        "buy_score": round(buy_score, 2),
        "sell_score": round(sell_score, 2),
    }

    return buy_score, sell_score, reasons, detail


def trigger_check(
    snap: IndicatorSnap,
    close_price: float,
    trigger_fns: list[Callable],
) -> tuple[bool, list[str]]:
    """15m 타이밍 트리거 — 하나라도 충족하면 True.

    Returns:
        (triggered, fired_names)
    """
    fired: list[str] = []
    for fn in trigger_fns:
        if fn(snap, close_price, None):
            fired.append(fn.__name__.replace("chk_", ""))
    return len(fired) > 0, fired

"""5분봉 전략 MTF 필터 로직 — strategy_5m_trigger에서 분리."""

from __future__ import annotations

from typing import Any

from core.analysis.signal_generator import SignalResult
from core.analysis.trend import get_higher_trend


async def apply_mtf_filter(
    signal_result: SignalResult,
    combined: dict[str, Any],
    market: str,
    ref_tfs: list[str],
) -> tuple[SignalResult, dict[str, Any]]:
    """MTF 필터 적용: 15m 충돌 + 1h 부스트/감쇠 + 추세 확인."""
    from core.strategy.strategy_5m_trigger import _evaluate_tf

    trend = await get_higher_trend(market, ref_tfs)
    combined["higher_trend"] = {
        "direction": trend.direction,
        "timeframe": trend.timeframe,
        "sma_20": trend.sma_20,
        "sma_50": trend.sma_50,
        "description": trend.description,
    }

    mtf_info: dict[str, Any] = {
        "conflict": False, "1h_agree": False,
        "1h_oppose": False, "detail": "",
    }

    for tf in ["15m", "1h"]:
        tf_signal, tf_detail = await _evaluate_tf(market, tf)
        if tf_detail:
            combined[f"tf_{tf}"] = tf_detail
        if tf_signal is None or tf_signal.signal_type == "HOLD":
            continue
        if tf == "15m" and tf_signal.signal_type != signal_result.signal_type:
            mtf_info["conflict"] = True
            mtf_info["detail"] = (
                f"15분봉 {tf_signal.signal_type} "
                f"({tf_signal.confidence:.1%}) vs "
                f"5분봉 {signal_result.signal_type}"
            )
            break
        if tf == "1h":
            if tf_signal.signal_type == signal_result.signal_type:
                mtf_info["1h_agree"] = True
            else:
                mtf_info["1h_oppose"] = True

    combined["mtf_filter"] = mtf_info
    signal_result = _apply_adjustments(signal_result, mtf_info, trend)
    return signal_result, combined


def _apply_adjustments(
    sig: SignalResult,
    mtf_info: dict[str, Any],
    trend: Any,
) -> SignalResult:
    """MTF 정보에 따른 시그널 조정."""
    if mtf_info["conflict"]:
        return SignalResult(
            "HOLD", 0.0,
            sig.reasons + [f"15분봉 반대 방향 ({mtf_info['detail']}) -- 보류"],
        )
    if mtf_info["1h_agree"]:
        boosted = min(sig.confidence * 1.1, 1.0)
        sig = SignalResult(
            sig.signal_type, round(boosted, 4),
            sig.reasons + [
                f"1시간봉 동의 -- 신뢰도 부스트 "
                f"{sig.confidence:.1%}->{boosted:.1%}",
            ],
        )
    elif mtf_info["1h_oppose"]:
        dampened = sig.confidence * 0.85
        sig = SignalResult(
            sig.signal_type, round(dampened, 4),
            sig.reasons + [
                f"1시간봉 반대 -- 신뢰도 감쇠 "
                f"{sig.confidence:.1%}->{dampened:.1%}",
            ],
        )
    if sig.signal_type == "BUY" and trend.direction == "DOWN":
        return SignalResult(
            "HOLD", 0.0,
            sig.reasons + [f"상위 추세 하락 ({trend.description}) -- 매수 보류"],
        )
    return sig

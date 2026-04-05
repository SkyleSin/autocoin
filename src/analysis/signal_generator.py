"""기술 지표 + 차트 패턴 + 지지/저항 → BUY / SELL / HOLD 시그널 종합 판단."""

from __future__ import annotations

from dataclasses import dataclass

from analysis.indicators import IndicatorSnapshot
from analysis.patterns import PatternAnalysis
from analysis.support_resistance import SRAnalysis


@dataclass(frozen=True)
class SignalResult:
    signal_type: str   # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 ~ 1.0
    reasons: list[str]


def evaluate(
    snap: IndicatorSnapshot,
    close_price: float,
    *,
    pattern_analysis: PatternAnalysis | None = None,
    sr_analysis: SRAnalysis | None = None,
) -> SignalResult:
    """지표 + 패턴 + 지지/저항으로 매매 시그널 종합 판단.

    점수 배분:
    - 기술 지표: 최대 1.0 (RSI 0.35, MACD 0.3, BB 0.25, SMA 0.1)
    - 차트 패턴: 최대 0.5 (신뢰도 기반)
    - 지지/저항: 최대 0.3
    """
    buy_score = 0.0
    sell_score = 0.0
    reasons: list[str] = []

    # ========== 1. 기술 지표 ==========

    # --- RSI ---
    if snap.rsi_14 is not None:
        if snap.rsi_14 < 30:
            buy_score += 0.35
            reasons.append(f"RSI 과매도 ({snap.rsi_14:.1f})")
        elif snap.rsi_14 < 40:
            buy_score += 0.15
            reasons.append(f"RSI 매수 접근 ({snap.rsi_14:.1f})")
        elif snap.rsi_14 > 70:
            sell_score += 0.35
            reasons.append(f"RSI 과매수 ({snap.rsi_14:.1f})")
        elif snap.rsi_14 > 60:
            sell_score += 0.15
            reasons.append(f"RSI 매도 접근 ({snap.rsi_14:.1f})")

    # --- MACD ---
    if snap.macd_histogram is not None:
        if (
            snap.macd_histogram > 0
            and snap.macd is not None
            and snap.macd_signal is not None
            and snap.macd > snap.macd_signal
        ):
            buy_score += 0.3
            reasons.append("MACD 골든크로스")
        elif (
            snap.macd_histogram < 0
            and snap.macd is not None
            and snap.macd_signal is not None
            and snap.macd < snap.macd_signal
        ):
            sell_score += 0.3
            reasons.append("MACD 데드크로스")

    # --- 볼린저밴드 ---
    if snap.bb_lower is not None and snap.bb_upper is not None:
        if close_price <= snap.bb_lower:
            buy_score += 0.25
            reasons.append("볼린저 하단 이탈")
        elif close_price >= snap.bb_upper:
            sell_score += 0.25
            reasons.append("볼린저 상단 돌파")

    # --- 이동평균 정배열/역배열 ---
    if snap.sma_20 is not None and snap.sma_50 is not None:
        if snap.sma_20 > snap.sma_50:
            buy_score += 0.1
            reasons.append("SMA 정배열 (20>50)")
        else:
            sell_score += 0.1
            reasons.append("SMA 역배열 (20<50)")

    # ========== 2. 차트 패턴 ==========

    if pattern_analysis and pattern_analysis.patterns:
        for p in pattern_analysis.patterns:
            # 신뢰도(50~100) → 점수(0.25~0.5)
            score = p.confidence / 100 * 0.5
            if p.signal == "BUY":
                buy_score += score
                reasons.append(f"패턴: {p.name_ko} ({p.confidence}%)")
            elif p.signal == "SELL":
                sell_score += score
                reasons.append(f"패턴: {p.name_ko} ({p.confidence}%)")
            # 가장 강한 패턴 1개만 반영
            break

    # ========== 3. 지지/저항 ==========

    if sr_analysis and sr_analysis.signal != "NONE":
        sr_sig = sr_analysis.signal
        if sr_sig == "SUPPORT_BOUNCE":
            buy_score += 0.3
            reasons.append(
                f"지지선 반등 ({sr_analysis.nearest_support:,.0f})"
                if sr_analysis.nearest_support
                else "지지선 반등"
            )
        elif sr_sig == "SUPPORT_BREAK":
            sell_score += 0.3
            reasons.append(
                f"지지선 이탈 ({sr_analysis.nearest_support:,.0f})"
                if sr_analysis.nearest_support
                else "지지선 이탈"
            )
        elif sr_sig == "RESISTANCE_BREAK":
            buy_score += 0.25
            reasons.append(
                f"저항선 돌파 ({sr_analysis.nearest_resistance:,.0f})"
                if sr_analysis.nearest_resistance
                else "저항선 돌파"
            )
        elif sr_sig == "RESISTANCE_REJECT":
            sell_score += 0.25
            reasons.append(
                f"저항선 저항 ({sr_analysis.nearest_resistance:,.0f})"
                if sr_analysis.nearest_resistance
                else "저항선 저항"
            )

    # ========== 최종 판단 ==========

    net = buy_score - sell_score
    threshold = 0.3

    if net >= threshold:
        confidence = min(buy_score, 1.0)
        return SignalResult("BUY", round(confidence, 2), reasons)
    elif net <= -threshold:
        confidence = min(sell_score, 1.0)
        return SignalResult("SELL", round(confidence, 2), reasons)
    else:
        confidence = round(1.0 - max(buy_score, sell_score), 2)
        if not reasons:
            reasons.append("시그널 없음")
        return SignalResult("HOLD", confidence, reasons)

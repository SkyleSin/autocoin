"""기술 지표 + 차트 패턴 + 지지/저항 → BUY / SELL / HOLD 시그널 종합 판단.

3단계 구조:
  1단계 (트리거): 검증된 패턴 또는 지지/저항 이벤트
  2단계 (확인): MACD 전환 + RSI 극단 + 볼린저 + 거래량
  3단계 (게이트): SMA 추세 방향 필터 — BUY는 상승추세 필수, SELL은 하락추세 필수
"""

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


# 거래량 확인 기준: 평균 대비 1.5배 이상이면 "의미 있는 거래량"
_VOLUME_CONFIRM_RATIO = 1.5


def evaluate(
    snap: IndicatorSnapshot,
    close_price: float,
    *,
    pattern_analysis: PatternAnalysis | None = None,
    sr_analysis: SRAnalysis | None = None,
) -> SignalResult:
    """3단계 시그널 판단.

    점수 배분:
    - 트리거 (패턴): 최대 0.5
    - 트리거 (지지/저항): 최대 0.3
    - 확인 (MACD 전환): 0.3
    - 확인 (RSI 극단): 0.35
    - 확인 (볼린저): 0.25
    - 거래량: 돌파/이탈 시그널 필수 확인
    - SMA: 추세 방향 게이트
    """
    buy_score = 0.0
    sell_score = 0.0
    reasons: list[str] = []
    has_trigger = False

    vol_ratio = snap.volume_ratio
    has_volume = vol_ratio is not None and vol_ratio >= _VOLUME_CONFIRM_RATIO

    # ========== 1단계: 트리거 (검증된 패턴 + 지지/저항) ==========

    # --- 차트 패턴 (활성 패턴만 트리거) ---
    if pattern_analysis and pattern_analysis.patterns:
        for p in pattern_analysis.patterns:
            if not p.active:
                continue
            score = p.confidence / 100 * 0.5
            if p.signal == "BUY":
                buy_score += score
                reasons.append(f"패턴: {p.name_ko} ({p.confidence}%)")
                has_trigger = True
            elif p.signal == "SELL":
                sell_score += score
                reasons.append(f"패턴: {p.name_ko} ({p.confidence}%)")
                has_trigger = True
            break  # 가장 강한 활성 패턴 1개만

    # --- 지지/저항 (돌파/이탈은 거래량 확인 필요) ---
    if sr_analysis and sr_analysis.signal != "NONE":
        sr_sig = sr_analysis.signal
        if sr_sig == "SUPPORT_BOUNCE":
            buy_score += 0.3
            reasons.append(
                f"지지선 반등 ({sr_analysis.nearest_support:,.0f})"
                if sr_analysis.nearest_support
                else "지지선 반등"
            )
            has_trigger = True
        elif sr_sig == "SUPPORT_BREAK":
            if has_volume:
                sell_score += 0.3
                reasons.append(
                    f"지지선 이탈 ({sr_analysis.nearest_support:,.0f})"
                    f" [거래량 {vol_ratio:.1f}x]"
                    if sr_analysis.nearest_support
                    else "지지선 이탈"
                )
                has_trigger = True
            else:
                reasons.append(
                    f"지지선 이탈 — 거래량 부족"
                    f" ({vol_ratio:.1f}x)" if vol_ratio else ""
                )
        elif sr_sig == "RESISTANCE_BREAK":
            if has_volume:
                buy_score += 0.25
                reasons.append(
                    f"저항선 돌파 ({sr_analysis.nearest_resistance:,.0f})"
                    f" [거래량 {vol_ratio:.1f}x]"
                    if sr_analysis.nearest_resistance
                    else "저항선 돌파"
                )
                has_trigger = True
            else:
                reasons.append(
                    f"저항선 돌파 — 거래량 부족"
                    f" ({vol_ratio:.1f}x)" if vol_ratio else ""
                )
        elif sr_sig == "RESISTANCE_REJECT":
            sell_score += 0.25
            reasons.append(
                f"저항선 저항 ({sr_analysis.nearest_resistance:,.0f})"
                if sr_analysis.nearest_resistance
                else "저항선 저항"
            )
            has_trigger = True

    # 트리거 없으면 HOLD
    if not has_trigger:
        return SignalResult("HOLD", 0.0, reasons or ["트리거 없음"])

    # ========== 2단계: 확인 (기술 지표) ==========

    # --- RSI ---
    if snap.rsi_14 is not None:
        if snap.rsi_14 < 30:
            buy_score += 0.35
            reasons.append(f"RSI 과매도 ({snap.rsi_14:.1f})")
        elif snap.rsi_14 > 70:
            sell_score += 0.35
            reasons.append(f"RSI 과매수 ({snap.rsi_14:.1f})")

    # --- MACD 전환 ---
    if snap.macd_histogram is not None and snap.macd_histogram_prev is not None:
        if snap.macd_histogram > 0 and snap.macd_histogram_prev <= 0:
            buy_score += 0.3
            reasons.append("MACD 골든크로스 전환")
        elif snap.macd_histogram < 0 and snap.macd_histogram_prev >= 0:
            sell_score += 0.3
            reasons.append("MACD 데드크로스 전환")

    # --- 볼린저밴드 ---
    if snap.bb_lower is not None and snap.bb_upper is not None:
        if close_price <= snap.bb_lower:
            buy_score += 0.25
            reasons.append("볼린저 하단 이탈")
        elif close_price >= snap.bb_upper:
            sell_score += 0.25
            reasons.append("볼린저 상단 돌파")

    # ========== 3단계: 최종 판단 + SMA 게이트 ==========

    net = buy_score - sell_score
    threshold = 0.3

    if net >= threshold:
        # BUY 게이트: 상승추세(SMA20 > SMA50) 필수
        if snap.sma_20 is not None and snap.sma_50 is not None:
            if snap.sma_20 < snap.sma_50:
                reasons.append("SMA 역배열 → 매수 차단")
                return SignalResult("HOLD", 0.0, reasons)
        confidence = min(buy_score, 1.0)
        return SignalResult("BUY", round(confidence, 2), reasons)
    elif net <= -threshold:
        # SELL 게이트: 하락추세(SMA20 < SMA50) 필수
        if snap.sma_20 is not None and snap.sma_50 is not None:
            if snap.sma_20 > snap.sma_50:
                reasons.append("SMA 정배열 → 매도 차단")
                return SignalResult("HOLD", 0.0, reasons)
        confidence = min(sell_score, 1.0)
        return SignalResult("SELL", round(confidence, 2), reasons)
    else:
        confidence = round(1.0 - max(buy_score, sell_score), 2)
        if not reasons:
            reasons.append("시그널 없음")
        return SignalResult("HOLD", confidence, reasons)

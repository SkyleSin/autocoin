"""기술 지표 + 지지/저항 + 차트 패턴 → BUY / SELL / HOLD 시그널 종합 판단.

4단계 구조:
  1단계 (트리거 — 필수): 지지/저항 이벤트 (S/R 없으면 HOLD)
  2단계 (확인 — 1개 이상): MACD 전환 / RSI 극단 / 볼린저 이탈
  3단계 (보조): 차트 패턴 (있으면 신뢰도 부스트, 없어도 시그널 가능)
  4단계 (게이트): SMA 추세 방향 필터
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
    """4단계 시그널 판단.

    점수 배분:
    - 1단계 트리거 (S/R — 필수): 0.35~0.45
    - 2단계 확인 (지표 — 1개 이상): MACD 0.30 / RSI 0.35 / BB 0.25
    - 3단계 보조 (패턴): 0.10~0.20 부스트
    - 4단계 게이트 (SMA): 추세 방향 필터
    - threshold: 0.4 (S/R + 지표 조합 필요)
    """
    buy_score = 0.0
    sell_score = 0.0
    reasons: list[str] = []

    vol_ratio = snap.volume_ratio
    has_volume = vol_ratio is not None and vol_ratio >= _VOLUME_CONFIRM_RATIO

    # ========== 1단계: 트리거 (지지/저항 — 필수) ==========

    has_sr_trigger = False

    if sr_analysis and sr_analysis.signal != "NONE":
        sr_sig = sr_analysis.signal
        if sr_sig == "SUPPORT_BOUNCE":
            buy_score += 0.40
            reasons.append(
                f"지지선 반등 ({sr_analysis.nearest_support:,.0f})"
                if sr_analysis.nearest_support
                else "지지선 반등"
            )
            has_sr_trigger = True
        elif sr_sig == "SUPPORT_BREAK":
            if has_volume:
                sell_score += 0.45
                reasons.append(
                    f"지지선 이탈 ({sr_analysis.nearest_support:,.0f})"
                    f" [거래량 {vol_ratio:.1f}x]"
                    if sr_analysis.nearest_support
                    else "지지선 이탈"
                )
                has_sr_trigger = True
            else:
                reasons.append(
                    f"지지선 이탈 — 거래량 부족"
                    f" ({vol_ratio:.1f}x)" if vol_ratio else ""
                )
        elif sr_sig == "RESISTANCE_BREAK":
            if has_volume:
                buy_score += 0.40
                reasons.append(
                    f"저항선 돌파 ({sr_analysis.nearest_resistance:,.0f})"
                    f" [거래량 {vol_ratio:.1f}x]"
                    if sr_analysis.nearest_resistance
                    else "저항선 돌파"
                )
                has_sr_trigger = True
            else:
                reasons.append(
                    f"저항선 돌파 — 거래량 부족"
                    f" ({vol_ratio:.1f}x)" if vol_ratio else ""
                )
        elif sr_sig == "RESISTANCE_REJECT":
            sell_score += 0.35
            reasons.append(
                f"저항선 저항 ({sr_analysis.nearest_resistance:,.0f})"
                if sr_analysis.nearest_resistance
                else "저항선 저항"
            )
            has_sr_trigger = True

    # S/R 트리거 없으면 HOLD (패턴만으로는 시그널 불가)
    if not has_sr_trigger:
        if not reasons:
            reasons.append("S/R 트리거 없음")
        return SignalResult("HOLD", 0.0, reasons)

    # ========== 2단계: 확인 (기술 지표 — 1개 이상 필요) ==========

    confirmations = 0

    # --- RSI ---
    if snap.rsi_14 is not None:
        if snap.rsi_14 < 30:
            buy_score += 0.35
            reasons.append(f"RSI 과매도 ({snap.rsi_14:.1f})")
            confirmations += 1
        elif snap.rsi_14 > 70:
            sell_score += 0.35
            reasons.append(f"RSI 과매수 ({snap.rsi_14:.1f})")
            confirmations += 1

    # --- MACD 전환 ---
    if snap.macd_histogram is not None and snap.macd_histogram_prev is not None:
        if snap.macd_histogram > 0 and snap.macd_histogram_prev <= 0:
            buy_score += 0.3
            reasons.append("MACD 골든크로스 전환")
            confirmations += 1
        elif snap.macd_histogram < 0 and snap.macd_histogram_prev >= 0:
            sell_score += 0.3
            reasons.append("MACD 데드크로스 전환")
            confirmations += 1

    # --- 볼린저밴드 ---
    if snap.bb_lower is not None and snap.bb_upper is not None:
        if close_price <= snap.bb_lower:
            buy_score += 0.25
            reasons.append("볼린저 하단 이탈")
            confirmations += 1
        elif close_price >= snap.bb_upper:
            sell_score += 0.25
            reasons.append("볼린저 상단 돌파")
            confirmations += 1

    # 지표 확인 없으면 HOLD
    if confirmations == 0:
        reasons.append("지표 확인 없음 → 시그널 보류")
        return SignalResult("HOLD", 0.0, reasons)

    # ========== 3단계: 보조 (차트 패턴 — 신뢰도 부스트) ==========

    if pattern_analysis and pattern_analysis.patterns:
        for p in pattern_analysis.patterns:
            if not p.active:
                continue
            boost = p.confidence / 100 * 0.20
            if p.signal == "BUY":
                buy_score += boost
                reasons.append(f"패턴 보조: {p.name_ko} (+{boost:.0%})")
            elif p.signal == "SELL":
                sell_score += boost
                reasons.append(f"패턴 보조: {p.name_ko} (+{boost:.0%})")
            break  # 가장 강한 활성 패턴 1개만

    # ========== 4단계: 최종 판단 + SMA 게이트 ==========

    net = buy_score - sell_score
    threshold = 0.4

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

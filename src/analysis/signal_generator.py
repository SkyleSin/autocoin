"""적중률 기반 시그널 판단 — 베이지안 결합 + 멀티TF 필터.

구조:
  1단계: 지표 투표 (RSI, BB, MACD — 타임프레임별 활성 지표만)
  2단계: S/R 컨텍스트 (근처에 있으면 confidence 부스트)
  3단계: 베이지안 결합 → 실제 예상 적중률 산출
  4단계: BUY/SELL 비대칭 threshold 적용
"""

from __future__ import annotations

from dataclasses import dataclass

from analysis.indicators import IndicatorSnapshot
from analysis.signal_weights import Weight, bayesian_combine, get_weights
from analysis.support_resistance import SRAnalysis

# 거래량 확인 기준
_VOLUME_CONFIRM_RATIO = 1.5

# BUY/SELL 비대칭 threshold
BUY_THRESHOLD = 0.55
SELL_THRESHOLD = 0.53

# 최소 지표 수
MIN_INDICATORS = 2

# S/R 부스트 (적중률에 가산)
_SR_BOOST = 0.025          # S/R 근처 시 +2.5%p
_SR_VOLUME_BOOST = 0.015   # 거래량 확인 시 추가 +1.5%p
_SR_STRONG_BOOST = 0.01    # 터치 3회+ 시 추가 +1%p


@dataclass(frozen=True)
class SignalResult:
    signal_type: str   # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 ~ 1.0 (베이지안 적중률)
    reasons: list[str]


def evaluate(
    snap: IndicatorSnapshot,
    close_price: float,
    *,
    timeframe: str = "5m",
    sr_analysis: SRAnalysis | None = None,
    pattern_analysis: object | None = None,  # 하위 호환용, 사용 안 함
) -> SignalResult:
    """적중률 기반 시그널 판단.

    각 지표의 백테스트 적중률을 베이지안 결합하여
    confidence가 실제 예상 적중률을 의미하도록 산출.
    """
    weights = get_weights(timeframe)

    fired_buy: list[tuple[str, float]] = []   # (지표명, 적중률)
    fired_sell: list[tuple[str, float]] = []
    reasons: list[str] = []

    vol_ratio = snap.volume_ratio
    has_volume = vol_ratio is not None and vol_ratio >= _VOLUME_CONFIRM_RATIO

    # ========== 1단계: 지표 투표 ==========

    # --- RSI ---
    if snap.rsi_14 is not None:
        if snap.rsi_14 > 70:
            w = weights.get("rsi_overbought")
            if w and w.is_active:
                fired_sell.append((w.name, w.accuracy))
                reasons.append(f"RSI 과매수 ({snap.rsi_14:.1f}) [{w.accuracy:.1%}]")
        elif snap.rsi_14 < 30:
            w = weights.get("rsi_oversold")
            if w and w.is_active:
                fired_buy.append((w.name, w.accuracy))
                reasons.append(f"RSI 과매도 ({snap.rsi_14:.1f}) [{w.accuracy:.1%}]")

    # --- 볼린저밴드 ---
    if snap.bb_lower is not None and snap.bb_upper is not None:
        if close_price >= snap.bb_upper:
            w = weights.get("bb_upper")
            if w and w.is_active:
                fired_sell.append((w.name, w.accuracy))
                reasons.append(f"볼린저 상단 돌파 [{w.accuracy:.1%}]")
        elif close_price <= snap.bb_lower:
            w = weights.get("bb_lower")
            if w and w.is_active:
                fired_buy.append((w.name, w.accuracy))
                reasons.append(f"볼린저 하단 이탈 [{w.accuracy:.1%}]")

    # --- MACD 전환 ---
    if snap.macd_histogram is not None and snap.macd_histogram_prev is not None:
        if snap.macd_histogram < 0 and snap.macd_histogram_prev >= 0:
            w = weights.get("macd_dead")
            if w and w.is_active:
                fired_sell.append((w.name, w.accuracy))
                reasons.append(f"MACD 데드크로스 [{w.accuracy:.1%}]")
        elif snap.macd_histogram > 0 and snap.macd_histogram_prev <= 0:
            w = weights.get("macd_golden")
            if w and w.is_active:
                fired_buy.append((w.name, w.accuracy))
                reasons.append(f"MACD 골든크로스 [{w.accuracy:.1%}]")

    # 활성 지표 없으면 HOLD
    if not fired_buy and not fired_sell:
        return SignalResult("HOLD", 0.0, reasons or ["활성 지표 없음"])

    # ========== 2단계: S/R 컨텍스트 (부스트) ==========

    sr_boost_buy = 0.0
    sr_boost_sell = 0.0

    if sr_analysis and sr_analysis.signal != "NONE":
        sr_sig = sr_analysis.signal
        boost = _SR_BOOST
        if has_volume and sr_sig in ("SUPPORT_BREAK", "RESISTANCE_BREAK"):
            boost += _SR_VOLUME_BOOST

        # 강한 레벨 (터치 3회+) 추가 부스트
        for lv in sr_analysis.levels:
            if lv.touch_count >= 3:
                boost += _SR_STRONG_BOOST
                break

        if sr_sig in ("SUPPORT_BOUNCE", "RESISTANCE_BREAK"):
            sr_boost_buy = boost
            reasons.append(
                f"S/R {sr_sig} 부스트 (+{boost:.1%})"
            )
        elif sr_sig in ("RESISTANCE_REJECT", "SUPPORT_BREAK"):
            sr_boost_sell = boost
            reasons.append(
                f"S/R {sr_sig} 부스트 (+{boost:.1%})"
            )

    # ========== 3단계: 베이지안 결합 ==========

    buy_accuracies = [acc + sr_boost_buy for _, acc in fired_buy]
    sell_accuracies = [acc + sr_boost_sell for _, acc in fired_sell]

    buy_conf = bayesian_combine(buy_accuracies) if buy_accuracies else 0.0
    sell_conf = bayesian_combine(sell_accuracies) if sell_accuracies else 0.0

    # ========== 4단계: 비대칭 threshold ==========

    # SELL 우선 (SELL 지표가 더 신뢰할 수 있으므로)
    if sell_conf >= SELL_THRESHOLD and len(fired_sell) >= MIN_INDICATORS:
        # BUY도 동시에 높으면 충돌 → HOLD
        if buy_conf >= BUY_THRESHOLD and len(fired_buy) >= MIN_INDICATORS:
            reasons.append(f"BUY/SELL 충돌 ({buy_conf:.1%} vs {sell_conf:.1%})")
            return SignalResult("HOLD", max(buy_conf, sell_conf), reasons)
        return SignalResult("SELL", round(sell_conf, 4), reasons)

    if buy_conf >= BUY_THRESHOLD and len(fired_buy) >= MIN_INDICATORS:
        return SignalResult("BUY", round(buy_conf, 4), reasons)

    # SELL 지표 1개라도 강하면 (60%+) 단독 시그널 허용
    if fired_sell and not fired_buy:
        max_sell_acc = max(acc for _, acc in fired_sell)
        if max_sell_acc >= 0.58 and sell_conf >= SELL_THRESHOLD:
            reasons.append(f"강한 SELL 단독 ({max_sell_acc:.1%})")
            return SignalResult("SELL", round(sell_conf, 4), reasons)

    # threshold 미달
    conf = max(buy_conf, sell_conf) if (buy_conf or sell_conf) else 0.0
    if not reasons:
        reasons.append("threshold 미달")
    return SignalResult("HOLD", round(conf, 4), reasons)

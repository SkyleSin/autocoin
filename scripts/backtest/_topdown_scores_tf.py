"""TF별 지표 스코어링 함수 (4H, 1H) + 15m 트리거."""

from __future__ import annotations

# ── 최대 가능 점수 (실제 테이블과 동기화) ──
MAX_BUY_4H = 1.67 + 1.22 + 1.12 + 0.76 + 0.55 + 0.37 + 0.35 + 0.22 + 0.20
MAX_SELL_4H = 1.10 + 0.73 + 0.49 + 0.30 + 0.28 + 0.22
MAX_BUY_1H = 0.48 + 0.41 + 0.32 + 0.27 + 0.27
MAX_SELL_1H = 0.25 + 0.21


def _add(name, score, direction, buy, sell, fired):
    fired.append(f"{direction}:{name}({score:.2f})")
    if direction == "BUY":
        buy += score
    else:
        sell += score
    return buy, sell


def score_4h(snap, sr, close, buy, sell, fired):
    """4H 방향 게이트용 점수 계산."""
    if snap is None:
        return buy, sell, fired
    a = _add
    if snap.macd_histogram is not None and snap.macd_histogram_prev is not None:
        if snap.macd_histogram > 0 and snap.macd_histogram_prev <= 0:
            buy, sell = a("macd_골든크로스", 1.12, "BUY", buy, sell, fired)
        if snap.macd_histogram < 0 and snap.macd_histogram_prev >= 0:
            buy, sell = a("macd_데드크로스", 1.10, "SELL", buy, sell, fired)
        if snap.macd_histogram > snap.macd_histogram_prev:
            buy, sell = a("macd_hist_증가중", 0.55, "BUY", buy, sell, fired)
        if snap.macd_histogram < snap.macd_histogram_prev:
            buy, sell = a("macd_hist_감소중", 0.49, "SELL", buy, sell, fired)
    if snap.sma_20 and close > snap.sma_20:
        buy, sell = a("price>sma20", 0.37, "BUY", buy, sell, fired)
    if snap.sma_20 and close < snap.sma_20:
        buy, sell = a("price<sma20", 0.30, "SELL", buy, sell, fired)
    if snap.sma_50 and close > snap.sma_50:
        buy, sell = a("price>sma50", 0.35, "BUY", buy, sell, fired)
    if snap.sma_50 and close < snap.sma_50:
        buy, sell = a("price<sma50", 0.28, "SELL", buy, sell, fired)
    if snap.ema_12 and snap.ema_26 and snap.ema_12 > snap.ema_26:
        buy, sell = a("ema12>ema26", 0.22, "BUY", buy, sell, fired)
    if snap.bb_upper and snap.bb_lower and snap.bb_middle:
        bw = (snap.bb_upper - snap.bb_lower) / snap.bb_middle
        if bw < 0.02:
            buy, sell = a("bb_squeeze", 0.20, "BUY", buy, sell, fired)
    if snap.volume_ratio is not None and snap.volume_ratio < 0.5:
        buy, sell = a("vol<0.5x", 0.22, "SELL", buy, sell, fired)
    if sr is not None:
        if sr.signal == "SUPPORT_BOUNCE":
            buy, sell = a("sr_지지반등", 1.67, "BUY", buy, sell, fired)
        elif sr.signal == "RESISTANCE_BREAK":
            buy, sell = a("sr_저항돌파", 1.22, "BUY", buy, sell, fired)
        elif sr.signal == "RESISTANCE_REJECT":
            buy, sell = a("sr_저항거부", 0.73, "SELL", buy, sell, fired)
        if sr.distance_to_support_pct is not None and sr.distance_to_support_pct <= 0.3:
            buy, sell = a("sr_지지근접_0.3%", 0.76, "BUY", buy, sell, fired)
    return buy, sell, fired


def score_1h(snap, sr, close, buy, sell, fired):
    """1H 신뢰도 부스트용 점수 계산 (BUY/SELL 모두 — multiplier 산출에 사용)."""
    if snap is None:
        return buy, sell, fired
    a = _add
    if snap.macd_histogram is not None and snap.macd_histogram_prev is not None:
        if snap.macd_histogram > 0 and snap.macd_histogram_prev <= 0:
            buy, sell = a("1h_macd_골든", 0.32, "BUY", buy, sell, fired)
        if snap.macd_histogram < 0 and snap.macd_histogram_prev >= 0:
            buy, sell = a("1h_macd_데드", 0.25, "SELL", buy, sell, fired)
        if snap.macd_histogram > snap.macd_histogram_prev:
            buy, sell = a("1h_macd_증가", 0.27, "BUY", buy, sell, fired)
        if snap.macd_histogram < snap.macd_histogram_prev:
            buy, sell = a("1h_macd_감소", 0.21, "SELL", buy, sell, fired)
    if sr is not None:
        if sr.signal == "RESISTANCE_BREAK":
            buy, sell = a("1h_sr_저항돌파", 0.48, "BUY", buy, sell, fired)
        elif sr.signal == "SUPPORT_BOUNCE":
            buy, sell = a("1h_sr_지지반등", 0.27, "BUY", buy, sell, fired)
    if snap.bb_upper and snap.bb_lower and snap.bb_middle:
        bw = (snap.bb_upper - snap.bb_lower) / snap.bb_middle
        if bw > 0.08:
            buy, sell = a("1h_bb_확장", 0.41, "BUY", buy, sell, fired)
    return buy, sell, fired


def trigger_15m(snap, close: float, direction: str) -> tuple[bool, list[str]]:
    """15m 타이밍 트리거 — 하나라도 충족하면 True.

    Returns:
        (triggered, fired_names)
    """
    if snap is None:
        return False, []
    fired: list[str] = []
    if direction == "BUY":
        if snap.rsi_14 is not None:
            if snap.rsi_14 < 50:
                fired.append(f"15m_rsi<50({snap.rsi_14:.1f})")
            if snap.rsi_14 < 45:
                fired.append(f"15m_rsi<45({snap.rsi_14:.1f})")
            if snap.rsi_14 < 40:
                fired.append(f"15m_rsi<40({snap.rsi_14:.1f})")
        if snap.bb_lower is not None and close <= snap.bb_lower:
            fired.append("15m_bb하단이탈")
        if snap.macd_histogram is not None and snap.macd_histogram_prev is not None:
            if snap.macd_histogram > snap.macd_histogram_prev:
                fired.append("15m_macd_rising")
    else:  # SELL
        if snap.rsi_14 is not None:
            if snap.rsi_14 > 50:
                fired.append(f"15m_rsi>50({snap.rsi_14:.1f})")
            if snap.rsi_14 > 55:
                fired.append(f"15m_rsi>55({snap.rsi_14:.1f})")
            if snap.rsi_14 > 60:
                fired.append(f"15m_rsi>60({snap.rsi_14:.1f})")
        if snap.macd_histogram is not None and snap.macd_histogram_prev is not None:
            if snap.macd_histogram < snap.macd_histogram_prev:
                fired.append("15m_macd_falling")
    return len(fired) > 0, fired

"""LLM 판단용 프롬프트 템플릿."""

from __future__ import annotations

from typing import Any


def build_analysis_prompt(
    market: str,
    close_price: float,
    indicators: dict[str, Any],
    technical_signal: str,
    technical_reasons: list[str],
    *,
    timeframe: str = "",
    candle_count: int = 0,
) -> str:
    """기술 지표 + 패턴 + 지지/저항 + 1차 시그널을 LLM 프롬프트로 변환."""

    # --- 기술 지표 ---
    ind_lines = []
    label_map = {
        "rsi_14": "RSI(14)",
        "macd": "MACD",
        "macd_signal": "MACD Signal",
        "macd_histogram": "MACD Histogram",
        "bb_upper": "볼린저 상단",
        "bb_middle": "볼린저 중간",
        "bb_lower": "볼린저 하단",
        "sma_20": "SMA 20",
        "sma_50": "SMA 50",
        "ema_12": "EMA 12",
        "ema_26": "EMA 26",
    }
    for key, label in label_map.items():
        val = indicators.get(key)
        if val is not None:
            fmt = (
                f"- {label}: {val:,.4f}"
                if isinstance(val, float)
                else f"- {label}: {val}"
            )
            ind_lines.append(fmt)
    indicators_text = "\n".join(ind_lines) if ind_lines else "- 데이터 없음"

    # --- 차트 패턴 ---
    patterns_text = "- 탐지된 패턴 없음"
    pat_data = indicators.get("patterns")
    if pat_data and isinstance(pat_data, dict):
        pat_list = pat_data.get("patterns", [])
        if pat_list:
            lines = []
            for p in pat_list:
                lines.append(
                    f"- {p['name_ko']} ({p['signal']}, "
                    f"신뢰도 {p['confidence']}%) — {p['description']}"
                )
            patterns_text = "\n".join(lines)

    # --- 지지/저항 ---
    sr_text = "- 탐지된 지지/저항선 없음"
    sr_data = indicators.get("support_resistance")
    if sr_data and isinstance(sr_data, dict):
        sr_lines = []
        levels = sr_data.get("levels", [])
        for lv in levels[:4]:
            sr_lines.append(
                f"- {lv['type']}: {lv['price']:,.0f} KRW "
                f"(터치 {lv['touches']}회, 강도 {lv['strength']})"
            )
        sup = sr_data.get("nearest_support")
        res = sr_data.get("nearest_resistance")
        dist_sup = sr_data.get("distance_to_support_pct")
        dist_res = sr_data.get("distance_to_resistance_pct")
        if sup:
            sr_lines.append(
                f"- 가장 가까운 지지선: {sup:,.0f} (거리 {dist_sup}%)"
            )
        if res:
            sr_lines.append(
                f"- 가장 가까운 저항선: {res:,.0f} (거리 {dist_res}%)"
            )
        sr_signal = sr_data.get("signal", "NONE")
        if sr_signal != "NONE":
            sig_map = {
                "SUPPORT_BOUNCE": "지지선 반등 (매수 유리)",
                "SUPPORT_BREAK": "지지선 이탈 (매도 주의)",
                "RESISTANCE_BREAK": "저항선 돌파 (매수 유리)",
                "RESISTANCE_REJECT": "저항선 저항 (매도 주의)",
            }
            sr_lines.append(f"- 시그널: {sig_map.get(sr_signal, sr_signal)}")
        if sr_lines:
            sr_text = "\n".join(sr_lines)

    # --- 상위 타임프레임 추세 ---
    trend_text = "- 참조 데이터 없음"
    trend_data = indicators.get("higher_trend")
    if trend_data and isinstance(trend_data, dict):
        trend_text = (
            f"- 방향: {trend_data.get('direction', 'NEUTRAL')}\n"
            f"- {trend_data.get('description', '')}"
        )

    # --- 1차 분석 결과 ---
    reasons_text = "\n".join(f"- {r}" for r in technical_reasons)

    # --- 타임프레임 컨텍스트 ---
    _TF_LABELS = {
        "1m": "1분봉", "3m": "3분봉", "5m": "5분봉", "10m": "10분봉",
        "15m": "15분봉", "30m": "30분봉", "1h": "1시간봉", "4h": "4시간봉",
    }
    _TF_MINUTES = {
        "1m": 1, "3m": 3, "5m": 5, "10m": 10,
        "15m": 15, "30m": 30, "1h": 60, "4h": 240,
    }
    tf_label = _TF_LABELS.get(timeframe, timeframe) if timeframe else "알 수 없음"
    analysis_hours = ""
    if timeframe and candle_count > 0:
        minutes = _TF_MINUTES.get(timeframe, 0) * candle_count
        if minutes > 0:
            analysis_hours = f" (약 {minutes // 60}시간, {minutes / 60 / 24:.1f}일치 데이터)"

    return f"""당신은 암호화폐 트레이딩 전문 분석가입니다.
아래 분석 결과를 종합 검토하고, 최종 매매 판단을 내려주세요.

## 마켓 정보
- 마켓: {market}
- 현재가: {close_price:,.0f} KRW
- 분석 타임프레임: {tf_label} × {candle_count}개{analysis_hours}

## 기술 지표
{indicators_text}

## 차트 패턴
{patterns_text}

## 지지/저항선
{sr_text}

## 상위 타임프레임 추세
{trend_text}

## 1차 기술 분석 결과
- 시그널: {technical_signal}
- 판단 근거:
{reasons_text}

## 지시사항
1. 기술 지표, 차트 패턴, 지지/저항선을 종합적으로 분석하세요.
2. 1차 분석 결과에 동의하는지, 다른 관점이 있는지 판단하세요.
3. 단기 노이즈에 반응하지 마세요. 명확한 추세와 강한 시그널이 있을 때만 BUY/SELL을 판단하세요.
4. 애매하거나 확신이 없으면 반드시 HOLD를 선택하세요. 과도한 매매보다 관망이 낫습니다.
5. 반드시 아래 JSON 형식으로만 응답하세요.

{{"decision": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reason": "판단 근거 1~2문장"}}"""

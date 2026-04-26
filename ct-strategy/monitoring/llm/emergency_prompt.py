"""긴급 지지선 이탈 판단용 LLM 프롬프트."""

from __future__ import annotations

from typing import Any


def build_emergency_prompt(
    market: str,
    current_price: float,
    support_level: float,
    breach_pct: float,
    consecutive_minutes: int,
    indicators: dict[str, Any] | None = None,
) -> str:
    """긴급 매도 판단 프롬프트. 손절 vs 반등 가능성에 집중."""

    ind_text = "- 데이터 없음"
    if indicators:
        lines = []
        for key, label in [
            ("rsi_14", "RSI(14)"),
            ("macd_histogram", "MACD 히스토그램"),
            ("sma_20", "SMA 20"),
            ("sma_50", "SMA 50"),
            ("volume_ratio", "거래량 비율"),
        ]:
            val = indicators.get(key)
            if val is not None:
                lines.append(
                    f"- {label}: {val:,.4f}" if isinstance(val, float)
                    else f"- {label}: {val}",
                )
        if lines:
            ind_text = "\n".join(lines)

    pattern_text = "- 없음"
    pat_data = indicators.get("patterns") if indicators else None
    if pat_data and isinstance(pat_data, dict):
        pat_list = pat_data.get("patterns", [])
        if pat_list:
            pattern_text = "\n".join(
                f"- {p['name_ko']} ({p['signal']}, {p['confidence']}%)"
                for p in pat_list[:3]
            )

    return f"""당신은 암호화폐 리스크 관리 전문가입니다.
보유 중인 코인이 지지선을 이탈했습니다. 손절해야 하는지, 반등을 기대할 수 있는지 판단하세요.

## 긴급 상황
- 마켓: {market}
- 현재가: {current_price:,.0f} KRW
- 지지선: {support_level:,.0f} KRW
- 이탈 폭: {breach_pct:.1f}%
- 이탈 지속: {consecutive_minutes}분 연속

## 최근 기술 지표
{ind_text}

## 차트 패턴
{pattern_text}

## 판단 기준
1. 거래량이 급증하며 하락 중이면 → 추가 하락 가능성 높음 → SELL
2. RSI 극과매도 + 거래량 감소 → 반등 가능성 → HOLD
3. 이탈 폭 3% 이상이면 추세 전환 가능성 → SELL 우선
4. 이미 패턴(머리어깨, 쌍봉 등)이 확인된 상태면 → SELL
5. 확신 없으면 SELL을 선택하세요. 손실 최소화가 최우선입니다.

반드시 아래 JSON 형식으로만 응답하세요.

{{"decision": "SELL/HOLD", "confidence": 0.0~1.0, "reason": "판단 근거 1~2문장"}}"""

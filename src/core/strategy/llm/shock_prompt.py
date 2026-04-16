"""뉴스 충격 감지 전용 LLM 프롬프트 — 멀티TF 지표 + 포지션 정보 포함."""

from __future__ import annotations

from typing import Any


def _fmt_indicators(data: dict[str, Any] | None, tf: str) -> str:
    """indicator_snapshots 데이터를 읽기 좋게 포맷."""
    if not data:
        return f"  {tf}: 데이터 없음"

    lines = [f"  [{tf}]"]
    for key, label in [
        ("rsi_14", "RSI(14)"),
        ("macd_histogram", "MACD히스토그램"),
        ("sma_20", "SMA20"),
        ("sma_50", "SMA50"),
        ("bb_upper", "BB상단"),
        ("bb_lower", "BB하단"),
        ("volume_ratio", "거래량비율"),
    ]:
        val = data.get(key)
        if val is not None:
            if isinstance(val, float):
                lines.append(f"    {label}: {val:,.4f}")
            else:
                lines.append(f"    {label}: {val}")

    sr = data.get("support_resistance")
    if sr and isinstance(sr, dict):
        support = sr.get("nearest_support")
        resistance = sr.get("nearest_resistance")
        sr_signal = sr.get("signal", "NONE")
        lines.append(f"    S/R시그널: {sr_signal}")
        if support:
            lines.append(f"    최근지지: {float(support):,.0f}")
        if resistance:
            lines.append(f"    최근저항: {float(resistance):,.0f}")

    return "\n".join(lines)


def build_shock_prompt(
    market: str,
    current_price: float,
    shock_level: str,
    shock_signals: list[str],
    volume_ratio: float,
    volatility_ratio: float,
    gap_pct: float,
    indicators_4h: dict[str, Any] | None = None,
    indicators_1h: dict[str, Any] | None = None,
    indicators_15m: dict[str, Any] | None = None,
    position_info: str = "보유 정보 없음",
) -> str:
    """충격 감지 시 LLM에게 전달할 프롬프트."""

    ind_text = "\n".join([
        _fmt_indicators(indicators_4h, "4H"),
        _fmt_indicators(indicators_1h, "1H"),
        _fmt_indicators(indicators_15m, "15m"),
    ])

    return f"""당신은 암호화폐 리스크 관리 전문가입니다.
시장에서 비정상적인 충격이 감지되었습니다. 현재 상태를 분석하고 방어 필요 여부를 판단하세요.

## 충격 감지 데이터
- 마켓: {market}
- 현재가: {current_price:,.0f} KRW
- 충격 강도: {shock_level}
- 감지된 신호: {', '.join(shock_signals)}
- 거래량: 평소 대비 {volume_ratio:.1f}배
- 변동성: 평소 대비 {volatility_ratio:.1f}배
- 가격 갭: {gap_pct:.1f}%

## 멀티 타임프레임 지표
{ind_text}

## 보유 포지션
{position_info}

## 판단 기준
1. 4H 추세가 상승이고 지지선 근처면 → 충격 흡수 가능성 → HOLD
2. 4H 추세 중립/하락 + 거래량 폭증 → 추세 전환 가능성 → SELL
3. RSI 극과매도(<30) + 거래량 감소 → 반등 가능성 → HOLD
4. MACD 하락 전환 + 지지선 이미 돌파 → 추가 하락 → SELL
5. 갭 5% 이상 패닉 상황 → SELL 우선
6. 확신이 없으면 SELL. 손실 최소화가 우선.

반드시 아래 JSON 형식으로만 응답하세요.

{{"decision": "SELL/HOLD", "confidence": 0.0~1.0, "reason": "판단 근거 1~2문장"}}"""

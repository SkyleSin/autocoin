"""시그널 결과 -> 텔레그램 메시지 포맷."""

from __future__ import annotations

from typing import Any


def _signal_emoji(signal_type: str) -> str:
    if signal_type == "BUY":
        return "🟢 매수"
    if signal_type == "SELL":
        return "🔴 매도"
    return "⚪ 관망"


def format_signal_alert(
    market: str,
    close_price: float,
    signal_type: str,
    confidence: float,
    reasons: list[str],
    indicators: dict[str, Any] | None = None,
) -> str:
    """매매 시그널 알림 메시지."""
    emoji = _signal_emoji(signal_type)
    reasons_text = "\n".join(f"  • {r}" for r in reasons)

    msg = f"""🚨 *매매 시그널 발생*

마켓: `{market}`
현재가: `{close_price:,.0f} KRW`

*{emoji} {signal_type}* (신뢰도: {confidence:.0%})

*판단 근거:*
{reasons_text}"""

    if indicators:
        msg += _format_pattern_sr(indicators)

    return msg


def _format_pattern_sr(indicators: dict[str, Any]) -> str:
    parts: list[str] = []
    pat = indicators.get("patterns", {})
    pat_list = pat.get("patterns", []) if isinstance(pat, dict) else []
    if pat_list:
        p = pat_list[0]
        parts.append(f"\n\n📐 패턴: {p['name_ko']} ({p['confidence']}%)")

    sr = indicators.get("support_resistance", {})
    if isinstance(sr, dict):
        sup = sr.get("nearest_support")
        res = sr.get("nearest_resistance")
        if sup:
            parts.append(f"\n📉 지지선: `{sup:,.0f}`")
        if res:
            parts.append(f"\n📈 저항선: `{res:,.0f}`")

    return "".join(parts)

"""시그널 결과 → 텔레그램 메시지 포맷 변환."""

from __future__ import annotations

from typing import Any


def _signal_emoji(signal_type: str) -> str:
    if signal_type == "BUY":
        return "🟢 매수"
    elif signal_type == "SELL":
        return "🔴 매도"
    return "⚪ 관망"


def format_technical_signal(
    market: str,
    close_price: float,
    signal_type: str,
    confidence: float,
    reasons: list[str],
    indicators: dict[str, Any] | None = None,
) -> str:
    """기술분석 시그널 알림 메시지."""
    emoji = _signal_emoji(signal_type)
    reasons_text = "\n".join(f"  • {r}" for r in reasons)

    msg = f"""📊 *기술분석 시그널*

*{emoji} {signal_type}* (확신도: {confidence:.0%})
마켓: `{market}`
현재가: `{close_price:,.0f} KRW`

*판단 근거:*
{reasons_text}"""

    # 패턴 정보 추가
    if indicators:
        pat = indicators.get("patterns", {})
        pat_list = pat.get("patterns", []) if isinstance(pat, dict) else []
        if pat_list:
            pat_lines = []
            for p in pat_list[:3]:
                pat_lines.append(
                    f"  • {p['name_ko']} ({p['signal']}, {p['confidence']}%)"
                )
            msg += "\n\n*차트 패턴:*\n" + "\n".join(pat_lines)

        sr = indicators.get("support_resistance", {})
        if isinstance(sr, dict):
            sup = sr.get("nearest_support")
            res = sr.get("nearest_resistance")
            sr_sig = sr.get("signal", "NONE")
            if sup or res:
                msg += "\n\n*지지/저항:*"
                if sup:
                    msg += f"\n  • 지지선: `{sup:,.0f}`"
                if res:
                    msg += f"\n  • 저항선: `{res:,.0f}`"
                if sr_sig != "NONE":
                    sig_map = {
                        "SUPPORT_BOUNCE": "지지선 반등",
                        "SUPPORT_BREAK": "⚠️ 지지선 이탈",
                        "RESISTANCE_BREAK": "저항선 돌파",
                        "RESISTANCE_REJECT": "⚠️ 저항선 저항",
                    }
                    msg += f"\n  • {sig_map.get(sr_sig, sr_sig)}"

    return msg


def format_llm_signal(
    market: str,
    close_price: float,
    decision: str,
    confidence: float,
    reason: str,
    model: str = "gemma3:4b",
) -> str:
    """LLM 판단 결과 알림 메시지."""
    emoji = _signal_emoji(decision)

    return f"""🤖 *AI 판단 결과* ({model})

*{emoji} {decision}* (확신도: {confidence:.0%})
마켓: `{market}`
현재가: `{close_price:,.0f} KRW`

*AI 분석:*
{reason}"""


def _llm_source_label(source: str) -> str:
    """LLM 소스 표시용 라벨."""
    labels = {
        "deepseek": "DeepSeek R1",
        "local_llm": "Ollama",
    }
    return labels.get(source, source)


def format_combined_alert(
    market: str,
    close_price: float,
    tech_signal: str,
    tech_confidence: float,
    tech_reasons: list[str],
    llm_decision: str,
    llm_confidence: float,
    llm_reason: str,
    indicators: dict[str, Any] | None = None,
    *,
    llm_source: str = "",
) -> str:
    """기술분석 + LLM 종합 알림 메시지."""
    tech_emoji = _signal_emoji(tech_signal)
    llm_emoji = _signal_emoji(llm_decision)
    reasons_text = "\n".join(f"  • {r}" for r in tech_reasons)

    source_label = f" [{_llm_source_label(llm_source)}]" if llm_source else ""

    msg = f"""🚨 *매매 시그널 발생*

마켓: `{market}`
현재가: `{close_price:,.0f} KRW`

*1차 기술분석:* {tech_emoji} {tech_signal} ({tech_confidence:.0%})
{reasons_text}

*2차 AI 판단{source_label}:* {llm_emoji} {llm_decision} ({llm_confidence:.0%})
{llm_reason}"""

    # 패턴/SR 정보 추가
    if indicators:
        pat = indicators.get("patterns", {})
        pat_list = pat.get("patterns", []) if isinstance(pat, dict) else []
        if pat_list:
            p = pat_list[0]
            msg += f"\n\n📐 패턴: {p['name_ko']} ({p['confidence']}%)"

        sr = indicators.get("support_resistance", {})
        if isinstance(sr, dict):
            sup = sr.get("nearest_support")
            res = sr.get("nearest_resistance")
            if sup:
                msg += f"\n📉 지지선: `{sup:,.0f}`"
            if res:
                msg += f"\n📈 저항선: `{res:,.0f}`"

    # 두 판단이 일치하면 강조
    if tech_signal == llm_decision and tech_signal in ("BUY", "SELL"):
        msg += f"\n\n✅ *기술분석과 AI 판단 일치 — {tech_signal} 강력 추천*"
    elif tech_signal != llm_decision:
        msg += "\n\n⚠️ *기술분석과 AI 판단 불일치 — 신중한 판단 필요*"

    return msg

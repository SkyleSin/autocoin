"""텔레그램 알림 E2E 테스트."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from telegram.bot import send_message, send_signal_alert
from telegram.messages import format_combined_alert, format_technical_signal
from utils.config import Settings


async def main() -> None:
    settings = Settings()

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID가 필요합니다.")
        return

    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    # 1) 단순 메시지 테스트
    print("=== 1. 단순 메시지 전송 ===")
    ok = await send_message(token, chat_id, "🔔 autocoin 텔레그램 연동 테스트")
    print(f"  결과: {'성공' if ok else '실패'}")

    # 2) 기술분석 시그널 메시지
    print("\n=== 2. 기술분석 시그널 메시지 ===")
    indicators = {
        "patterns": {
            "patterns": [
                {
                    "name": "triple_top",
                    "name_ko": "삼중천정",
                    "signal": "SELL",
                    "confidence": 100,
                    "description": "고점 3회 저항",
                },
            ],
        },
        "support_resistance": {
            "levels": [],
            "nearest_support": 101715000,
            "nearest_resistance": 101837000,
            "signal": "SUPPORT_BREAK",
            "distance_to_support_pct": 0.1,
            "distance_to_resistance_pct": 0.02,
        },
    }
    tech_msg = format_technical_signal(
        "KRW-BTC", 101820000, "SELL", 0.9,
        ["MACD 골든크로스", "SMA 역배열", "패턴: 삼중천정 (100%)", "지지선 이탈"],
        indicators,
    )
    ok = await send_signal_alert(token, chat_id, tech_msg)
    print(f"  결과: {'성공' if ok else '실패'}")

    # 3) 종합 알림 메시지 (기술분석 + LLM)
    print("\n=== 3. 종합 알림 메시지 ===")
    combined_msg = format_combined_alert(
        market="KRW-BTC",
        close_price=101820000,
        tech_signal="SELL",
        tech_confidence=0.9,
        tech_reasons=["MACD 골든크로스", "SMA 역배열", "패턴: 삼중천정 (100%)", "지지선 이탈"],
        llm_decision="SELL",
        llm_confidence=0.8,
        llm_reason="삼중천정 패턴과 지지선 이탈로 하락 가능성이 높습니다.",
        indicators=indicators,
    )
    ok = await send_signal_alert(token, chat_id, combined_msg)
    print(f"  결과: {'성공' if ok else '실패'}")

    print("\n=== 테스트 완료 — 텔레그램 확인해주세요 ===")


if __name__ == "__main__":
    asyncio.run(main())

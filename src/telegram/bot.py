"""텔레그램 Bot API 클라이언트 — httpx 기반, 경량."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


async def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "Markdown",
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """텔레그램 메시지 전송. 성공 시 True."""
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.is_success:
                logger.info("telegram: 메시지 전송 성공 (chat_id=%s)", chat_id)
                return True
            logger.warning(
                "telegram: 전송 실패 status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
    except Exception:
        logger.exception("telegram: 전송 중 오류")
        return False


async def send_photo(
    token: str,
    chat_id: str,
    image_bytes: bytes,
    *,
    caption: str = "",
    parse_mode: str = "Markdown",
    filename: str = "chart.png",
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """텔레그램 이미지 전송. 성공 시 True."""
    url = f"{TELEGRAM_API}/bot{token}/sendPhoto"
    data: dict[str, str] = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    files = {"photo": (filename, image_bytes, "image/png")}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, data=data, files=files)
            if resp.is_success:
                logger.info("telegram: 이미지 전송 성공 (chat_id=%s)", chat_id)
                return True
            logger.warning(
                "telegram: 이미지 전송 실패 status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
    except Exception:
        logger.exception("telegram: 이미지 전송 중 오류")
        return False


def make_signal_buttons(
    market: str, signal_type: str, close_price: float,
) -> dict[str, Any]:
    """시그널 알림용 인라인 버튼 생성."""
    side = "BUY" if signal_type == "BUY" else "SELL"
    action_label = "🟢 매수 실행" if side == "BUY" else "🔴 매도 실행"

    return {
        "inline_keyboard": [
            [
                {
                    "text": action_label,
                    "callback_data": f"trade_execute:{market}:{side}:{close_price}",
                },
                {
                    "text": "⏭️ 패스",
                    "callback_data": "trade_pass",
                },
            ],
        ],
    }


async def send_signal_alert(
    token: str,
    chat_id: str,
    text: str,
    *,
    chart_image: bytes | None = None,
    market: str = "",
    signal_type: str = "",
    close_price: float = 0,
) -> bool:
    """시그널 알림 전송. BUY/SELL이면 인라인 버튼 포함."""
    buttons = None
    if signal_type in ("BUY", "SELL") and market:
        buttons = make_signal_buttons(market, signal_type, close_price)

    if chart_image:
        return await send_photo(
            token, chat_id, chart_image,
            caption=text, reply_markup=buttons,
        )
    return await send_message(token, chat_id, text, reply_markup=buttons)

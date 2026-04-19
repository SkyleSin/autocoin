"""텔레그램 Bot API 클라이언트 — httpx 기반."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
_MAX_MSG_LEN = 4096


def _split_text(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = current + ("\n" if current else "") + line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line[:limit]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


async def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "Markdown",
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """텔레그램 메시지 전송. 4096자 초과 시 자동 분할."""
    if len(text) > _MAX_MSG_LEN:
        chunks = _split_text(text, _MAX_MSG_LEN)
        return all([
            await send_message(token, chat_id, c, parse_mode=parse_mode)
            for c in chunks
        ])

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
                return True
            if resp.status_code == 400 and "parse entities" in resp.text:
                payload.pop("parse_mode", None)
                resp2 = await client.post(url, json=payload)
                if resp2.is_success:
                    return True
                logger.warning("telegram: 플레인 재전송 실패 %s", resp2.text[:200])
                return False
            logger.warning("telegram: 전송 실패 status=%s", resp.status_code)
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
    """텔레그램 이미지 전송."""
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
                return True
            if resp.status_code == 400 and "parse entities" in resp.text:
                data.pop("parse_mode", None)
                files_retry = {"photo": (filename, image_bytes, "image/png")}
                resp2 = await client.post(url, data=data, files=files_retry)
                if resp2.is_success:
                    return True
                logger.warning("telegram: 이미지 플레인 재전송 실패 %s", resp2.text[:200])
                return False
            logger.warning("telegram: 이미지 전송 실패 status=%s", resp.status_code)
            return False
    except Exception:
        logger.exception("telegram: 이미지 전송 중 오류")
        return False


async def answer_callback(token: str, callback_query_id: str, text: str = "") -> None:
    """콜백 쿼리 응답 (버튼 클릭 로딩 해제)."""
    url = f"{TELEGRAM_API}/bot{token}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={
                "callback_query_id": callback_query_id,
                "text": text,
            })
    except Exception:
        logger.warning("answerCallbackQuery 실패", exc_info=True)


def make_signal_buttons(
    market: str,
    signal_type: str,
    close_price: float,
    *,
    sell_recommended_pct: int = 0,
    sell_choices: list[int] | None = None,
    buy_amount_krw: float | None = None,
) -> dict[str, Any]:
    """시그널 알림용 인라인 버튼 생성."""
    if signal_type == "BUY":
        amount = int(buy_amount_krw) if buy_amount_krw else 0
        return {
            "inline_keyboard": [[
                {"text": "매수 실행",
                 "callback_data": f"trade_execute:{market}:BUY:{amount}"},
                {"text": "패스",
                 "callback_data": f"trade_pass:{market}"},
            ]],
        }

    choices = sell_choices or [20, 40, 60, 80, 100]
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for pct in choices:
        star = " *" if pct == sell_recommended_pct else ""
        row.append({
            "text": f"{pct}%{star}",
            "callback_data": f"trade_sell:{market}:{close_price}:{pct}",
        })
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "패스", "callback_data": f"trade_pass:{market}"}])
    return {"inline_keyboard": rows}

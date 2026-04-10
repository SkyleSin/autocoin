"""텔레그램 Bot API 클라이언트 — httpx 기반, 경량."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
_MAX_MSG_LEN = 4096


def _split_text(text: str, limit: int) -> list[str]:
    """텍스트를 줄 단위로 분할하여 limit 이내 청크 리스트 반환."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = current + ("\n" if current else "") + line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line[:limit]  # 단일 라인이 limit 초과 시 잘라냄
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
    """텔레그램 메시지 전송. 4096자 초과 시 자동 분할. 성공 시 True."""
    if len(text) > _MAX_MSG_LEN:
        # 줄 단위로 분할하여 순차 전송
        chunks = _split_text(text, _MAX_MSG_LEN)
        ok = True
        for chunk in chunks:
            if not await send_message(token, chat_id, chunk, parse_mode=parse_mode):
                ok = False
        return ok

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
            # Markdown 파싱 실패 시 플레인 텍스트로 재시도
            if resp.status_code == 400 and "parse entities" in resp.text:
                logger.warning("telegram: Markdown 파싱 실패, 플레인 텍스트로 재시도")
                payload.pop("parse_mode", None)
                resp2 = await client.post(url, json=payload)
                if resp2.is_success:
                    logger.info("telegram: 플레인 텍스트 재전송 성공 (chat_id=%s)", chat_id)
                    return True
                logger.warning(
                    "telegram: 플레인 재전송도 실패 status=%s body=%s",
                    resp2.status_code, resp2.text[:200],
                )
                return False
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
            # Markdown 파싱 실패 시 플레인 텍스트로 재시도
            if resp.status_code == 400 and "parse entities" in resp.text:
                logger.warning("telegram: 이미지 캡션 Markdown 파싱 실패, 플레인 텍스트로 재시도")
                data.pop("parse_mode", None)
                files_retry = {"photo": (filename, image_bytes, "image/png")}
                resp2 = await client.post(url, data=data, files=files_retry)
                if resp2.is_success:
                    logger.info("telegram: 이미지 플레인 재전송 성공 (chat_id=%s)", chat_id)
                    return True
                logger.warning(
                    "telegram: 이미지 플레인 재전송도 실패 status=%s body=%s",
                    resp2.status_code, resp2.text[:200],
                )
                return False
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
    market: str,
    signal_type: str,
    close_price: float,
    *,
    sell_recommended_pct: int = 0,
    sell_choices: list[int] | None = None,
) -> dict[str, Any]:
    """시그널 알림용 인라인 버튼 생성."""
    if signal_type == "BUY":
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "🟢 매수 실행",
                        "callback_data": f"trade_execute:{market}:BUY:{close_price}",
                    },
                    {
                        "text": "⏭️ 패스",
                        "callback_data": f"trade_pass:{market}",
                    },
                ],
            ],
        }

    # SELL — 20% 단위 선택지 + 추천 표시
    choices = sell_choices or [20, 40, 60, 80, 100]
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for pct in choices:
        star = " ⭐" if pct == sell_recommended_pct else ""
        row.append({
            "text": f"{'🔴 ' if pct == sell_recommended_pct else ''}{pct}%{star}",
            "callback_data": f"trade_sell:{market}:{close_price}:{pct}",
        })
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "⏭️ 패스", "callback_data": f"trade_pass:{market}"}])

    return {"inline_keyboard": rows}


async def send_signal_alert(
    token: str,
    chat_id: str,
    text: str,
    *,
    chart_image: bytes | None = None,
    market: str = "",
    signal_type: str = "",
    close_price: float = 0,
    sell_recommended_pct: int = 0,
    sell_choices: list[int] | None = None,
) -> bool:
    """시그널 알림 전송. BUY/SELL이면 인라인 버튼 포함."""
    buttons = None
    if signal_type in ("BUY", "SELL") and market:
        buttons = make_signal_buttons(
            market, signal_type, close_price,
            sell_recommended_pct=sell_recommended_pct,
            sell_choices=sell_choices,
        )

    if chart_image:
        return await send_photo(
            token, chat_id, chart_image,
            caption=text, reply_markup=buttons,
        )
    return await send_message(token, chat_id, text, reply_markup=buttons)

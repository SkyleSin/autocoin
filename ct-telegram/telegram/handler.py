"""텔레그램 명령어/콜백 핸들러 — 라우팅."""

from __future__ import annotations

import logging
from typing import Any

from infra.api.user import get_user_by_telegram
from telegram.bot import send_message
from telegram.callback_handlers import handle_callback
from telegram.cmd_asset import cmd_add_asset, cmd_assets, cmd_remove_asset
from telegram.cmd_info import cmd_history, cmd_report, cmd_status
from utils.config import Settings

logger = logging.getLogger(__name__)


async def handle_update(update: dict[str, Any], settings: Settings) -> None:
    """텔레그램 업데이트 하나를 처리."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if "callback_query" in update:
        await handle_callback(update["callback_query"], settings)
        return

    msg = update.get("message")
    if not msg or "text" not in msg:
        return

    text = msg["text"].strip()
    msg_chat_id = str(msg["chat"]["id"])

    user = await get_user_by_telegram(int(msg_chat_id))
    if user is None:
        logger.warning("telegram: 미등록 chat_id=%s 무시", msg_chat_id)
        return

    await _route_command(text, token, chat_id, settings)


async def _route_command(
    text: str, token: str, chat_id: str, settings: Settings,
) -> None:
    """명령어 라우팅."""
    if text.startswith("/start") or text.startswith("/help"):
        await _cmd_help(token, chat_id)
    elif text.startswith("/a") and text.rstrip() in ("/a", "/assets"):
        await cmd_assets(token, chat_id)
    elif text.startswith("/add "):
        await cmd_add_asset(token, chat_id, text[5:].strip().upper())
    elif text.startswith("/rm "):
        await cmd_remove_asset(token, chat_id, text[4:].strip().upper())
    elif text.startswith("/remove "):
        await cmd_remove_asset(token, chat_id, text[8:].strip().upper())
    elif text.startswith("/h") and text.rstrip() in ("/h", "/history"):
        await cmd_history(token, chat_id)
    elif text.startswith("/s") and text.rstrip() in ("/s", "/status"):
        await cmd_status(token, chat_id, settings)
    elif text.startswith("/r") and text.rstrip() in ("/r", "/report"):
        await cmd_report(token, chat_id, settings)
    else:
        await send_message(token, chat_id, "알 수 없는 명령어입니다. /help 를 입력하세요.")


async def _cmd_help(token: str, chat_id: str) -> None:
    msg = """🤖 *autocoin 봇 명령어*

*자산 관리:*
/a — 추적 자산 목록
/add KRW-BTC — 추적 자산 추가
/rm KRW-BTC — 추적 자산 제거

*조회:*
/s — 시스템 상태
/h — 매매 이력
/r — 리포트

_/balance, /markets — 준비 중_"""
    await send_message(token, chat_id, msg)

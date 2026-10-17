"""텔레그램 명령어/콜백 핸들러 — 라우팅."""

from __future__ import annotations

import logging
from typing import Any

from infra.api.user import get_user_by_telegram
from utils.config import Settings

from telegram.bot import send_message
from telegram.callback_handlers import handle_callback
from telegram.cmd_asset import cmd_add_asset, cmd_remove_asset, cmd_watchlist
from telegram.cmd_info import cmd_balance, cmd_history, cmd_markets, cmd_report

logger = logging.getLogger(__name__)


async def handle_update(update: dict[str, Any], settings: Settings) -> None:
    """텔레그램 업데이트 하나를 처리."""
    token = settings.telegram_bot_token

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

    await _route_command(text, token, msg_chat_id, settings, user)


async def _route_command(
    text: str, token: str, chat_id: str, settings: Settings,
    user: dict,
) -> None:
    """명령어 라우팅."""
    user_id = user["id"]

    if text.startswith("/start") or text.startswith("/help"):
        await _cmd_help(token, chat_id)
    elif text.rstrip() in ("/w", "/watchlist"):
        await cmd_watchlist(token, chat_id, user_id)
    elif text.startswith("/add "):
        await cmd_add_asset(token, chat_id, user_id, text[5:].strip().upper())
    elif text.startswith("/rm "):
        await cmd_remove_asset(token, chat_id, user_id, text[4:].strip().upper())
    elif text.startswith("/remove "):
        await cmd_remove_asset(token, chat_id, user_id, text[8:].strip().upper())
    elif text.rstrip() in ("/h", "/history"):
        await cmd_history(token, chat_id, user_id)
    elif text.rstrip() in ("/r", "/report"):
        await cmd_report(token, chat_id, user_id)
    elif text.rstrip() in ("/b", "/balance"):
        await cmd_balance(token, chat_id, user_id)
    elif text.rstrip() in ("/m", "/markets"):
        await cmd_markets(token, chat_id)
    elif text.startswith("/markets "):
        await cmd_markets(token, chat_id, text[9:].strip())
    else:
        await send_message(token, chat_id, "�� 수 없는 명령어입니다. /help 를 입력하세요.")


async def _cmd_help(token: str, chat_id: str) -> None:
    msg = """🤖 *autocoin 봇 명령어*

*자산 관리:*
/w — 워치리스트
/add KRW-BTC — 워치리스트 추가
/rm KRW-BTC — 워치리스트 제거

*조회:*
/r — 손익 리포트
/h — 매매 이력
/b — 보유 자산 현황
/m — KRW 마켓 목록
/markets BTC — 마켓 검색"""
    await send_message(token, chat_id, msg)

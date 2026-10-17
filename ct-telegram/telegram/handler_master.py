"""마스터 봇 명령어 핸들러 — 라우팅."""

from __future__ import annotations

import logging
from typing import Any

from utils.config import Settings

from telegram.bot import send_message
from telegram.cmd_master import (
    cmd_ind,
    cmd_shadow,
    cmd_sig,
    cmd_sys,
    cmd_tracked,
    cmd_users,
)

logger = logging.getLogger(__name__)


async def handle_master_update(update: dict[str, Any], settings: Settings) -> None:
    """마스터 봇 업데이트 처리 — master_telegram_id만 허용."""
    msg = update.get("message")
    if not msg or "text" not in msg:
        return

    text = msg["text"].strip()
    chat_id = str(msg["chat"]["id"])

    if chat_id != str(settings.master_telegram_id):
        logger.warning("master: 비인가 접근 chat_id=%s", chat_id)
        return

    token = settings.master_bot_token
    await _route(text, token, chat_id, settings)


async def _route(text: str, token: str, chat_id: str, settings: Settings) -> None:
    if text.rstrip() in ("/start", "/help"):
        await _cmd_help(token, chat_id)
    elif text.rstrip() in ("/sys", "/system"):
        await cmd_sys(token, chat_id)
    elif text.rstrip() in ("/sig", "/signals"):
        await cmd_sig(token, chat_id)
    elif text.rstrip() in ("/ind", "/indicators"):
        await cmd_ind(token, chat_id)
    elif text.rstrip() in ("/tracked",):
        await cmd_tracked(token, chat_id)
    elif text.rstrip() in ("/users",):
        await cmd_users(token, chat_id)
    elif text.rstrip() in ("/shadow",):
        await cmd_shadow(token, chat_id)
    else:
        await send_message(token, chat_id, "알 수 없는 명령어. /help")


async def _cmd_help(token: str, chat_id: str) -> None:
    msg = """🛠 *autocoin 관리자 봇*

/sys — 시스템 상태
/sig — 시그널 요약 + 최근 20건
/ind — 지표별 적중률 + 현재 스냅샷
/tracked — 추적 자산 + 현재가
/users — 유저 목록
/shadow — shadow 유저 성과"""
    await send_message(token, chat_id, msg)

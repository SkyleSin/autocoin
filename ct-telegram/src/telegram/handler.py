"""텔레그램 명령어/콜백 핸들러 — 라우팅."""

from __future__ import annotations

import logging
from typing import Any

from infra.api.user import get_user_by_telegram
from telegram.bot import send_message
from telegram.callback_handlers import handle_callback
from telegram.cmd_asset import _cmd_add_asset, _cmd_assets, _cmd_remove_asset
from telegram.cmd_info import (
    _cmd_balance,
    _cmd_history,
    _cmd_markets,
    _cmd_report,
    _cmd_report_detail,
    _cmd_status,
)
from telegram.cmd_risk import _cmd_risk
from telegram.cmd_todo import _cmd_todo_add, _cmd_todo_done, _cmd_todo_list
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
    """명령어 텍스트를 적절한 핸들러로 라우팅."""
    if text.startswith("/start") or text.startswith("/help"):
        await _cmd_help(token, chat_id)
    elif text.startswith("/b") and text.rstrip() in ("/b", "/balance"):
        await _cmd_balance(token, chat_id, settings)
    elif text.startswith("/markets"):
        keyword = text[8:].strip().upper() if len(text) > 8 else ""
        await _cmd_markets(token, chat_id, settings, keyword)
    elif text.startswith("/a") and text.rstrip() in ("/a", "/assets"):
        await _cmd_assets(token, chat_id)
    elif text.startswith("/add "):
        await _cmd_add_asset(token, chat_id, text[5:].strip().upper())
    elif text.startswith("/rm "):
        await _cmd_remove_asset(token, chat_id, text[4:].strip().upper())
    elif text.startswith("/remove "):
        await _cmd_remove_asset(token, chat_id, text[8:].strip().upper())
    elif text.startswith("/risk"):
        await _cmd_risk(token, chat_id, settings, text)
    elif text.startswith("/h") and text.rstrip() in ("/h", "/history"):
        await _cmd_history(token, chat_id)
    elif text.startswith("/s") and text.rstrip() in ("/s", "/status"):
        await _cmd_status(token, chat_id, settings)
    elif text.startswith("/rd") or text.startswith("/report_detail"):
        await _cmd_report_detail(token, chat_id)
    elif text.startswith("/r") and text.rstrip() in ("/r", "/report"):
        await _cmd_report(token, chat_id, settings)
    elif text.startswith("/todo_done "):
        await _cmd_todo_done(token, chat_id, text[11:].strip())
    elif text.startswith("/todo_list") or text.rstrip() == "/todos":
        await _cmd_todo_list(token, chat_id)
    elif text.startswith("/todo "):
        await _cmd_todo_add(token, chat_id, text[6:].strip())
    else:
        await send_message(token, chat_id, "알 수 없는 명령어입니다. /help 를 입력하세요.")


async def _cmd_help(token: str, chat_id: str) -> None:
    msg = """🤖 *autocoin 봇 명령어*

*자산 관리:*
/a — 추적 자산 목록
/add KRW-BTC — 추적 자산 추가
/rm KRW-BTC — 추적 자산 제거
/markets BTC — 마켓 검색

*조회:*
/b — 잔고 조회
/s — 시스템 상태
/h — 매매 이력

*리스크 설정:*
/risk — 현재 리스크 설정
/risk max\\_pct 10 — 1회 매매 상한 (%)
/risk daily 20 — 일일 매매 횟수
/risk reserve 50000 — 최소 유지 잔고 (원)

*리포트:*
/r — 수익률 + 적중률 리포트
/rd — 상세 분석 리포트

*메모:*
/todo 내용 — TODO 추가
/todos — TODO 목록
/todo\\_done 번호 — 완료 처리"""
    await send_message(token, chat_id, msg)

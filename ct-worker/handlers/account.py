"""잔고 조회 핸들러 — 빗썸 계좌 잔고 조회."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bithumb.account import BithumbAccount
from infra.redis_pubsub import publish_to_telegram
from utils.config import Settings

logger = logging.getLogger(__name__)


def _fetch_sync(settings: Settings) -> list[dict[str, Any]]:
    """동기 빗썸 API 호출 — asyncio.to_thread()로 실행."""
    client = BithumbAccount(settings)
    try:
        return client.get_balances()
    finally:
        client.close()


async def handle_check_balance(
    payload: dict, settings: Settings,
) -> dict[str, Any] | None:
    """check_balance Job — 잔고 조회.

    payload:
      - chat_id (str, optional): 텔레그램 응답용 (직접 응답)
      - market (str, optional): 특정 코인 잔고 확인

    Returns:
        response_channel이 설정된 경우 잔고 dict 반환 (ct-advisor용).
        chat_id가 있으면 텔레그램 직접 응답 후 None 반환.
    """
    chat_id = payload.get("chat_id")

    accounts = await asyncio.to_thread(_fetch_sync, settings)
    if not accounts:
        logger.warning("check_balance: 잔고 조회 결과 없음")
        return {"accounts": [], "error": "empty"}

    # 텔레그램 잔고 조회 응답 (직접)
    if chat_id:
        lines = ["💰 *잔고 현황*\n"]
        for acc in accounts:
            currency = acc.get("currency", "?")
            balance = float(acc.get("balance", 0))
            locked = float(acc.get("locked", 0))
            if balance > 0 or locked > 0:
                lines.append(f"`{currency}`: {balance:,.4f} (묶임: {locked:,.4f})")
        await publish_to_telegram({
            "type": "text",
            "chat_id": chat_id,
            "text": "\n".join(lines),
            "parse_mode": "Markdown",
        })
        return None

    # ct-advisor용 — 잔고 데이터 반환
    result: dict[str, Any] = {"accounts": []}
    for acc in accounts:
        currency = acc.get("currency", "?")
        balance = float(acc.get("balance", 0))
        locked = float(acc.get("locked", 0))
        if balance > 0 or locked > 0:
            result["accounts"].append({
                "currency": currency,
                "balance": balance,
                "locked": locked,
            })

    logger.info("check_balance: %d개 자산 조회", len(result["accounts"]))
    return result


async def handle_get_balance(payload: dict, settings: Settings) -> None:
    """get_balance Job — 특정 통화 잔고 조회 (텔레그램 응답 전용)."""
    currency = payload.get("currency", "KRW")
    chat_id = payload.get("chat_id")

    accounts = await asyncio.to_thread(_fetch_sync, settings)
    acc = next(
        (a for a in accounts if a.get("currency", "").upper() == currency.upper()),
        None,
    )

    if chat_id:
        if acc:
            balance = float(acc.get("balance", 0))
            locked = float(acc.get("locked", 0))
            text = f"💰 `{currency}`: {balance:,.4f} (묶임: {locked:,.4f})"
        else:
            text = f"⚠️ `{currency}` 잔고 없음"
        await publish_to_telegram({
            "type": "text",
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })

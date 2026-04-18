"""텔레그램 자산 관리 명령어 핸들러."""

from __future__ import annotations

import logging

from infra.api.tracked_asset import add_tracked_asset, get_active_markets, update_tracked_asset
from infra.redis_queue import push_trade_job
from telegram.bot import send_message

logger = logging.getLogger(__name__)

_BACKFILL_TFS = ["15m", "1h", "4h", "1d"]


async def cmd_assets(token: str, chat_id: str) -> None:
    assets = await get_active_markets()
    if not assets:
        await send_message(
            token, chat_id, "📋 추적 중인 자산이 없습니다.\n/add KRW-BTC 로 추가하세요.",
        )
        return

    lines = ["📋 *추적 자산 목록*\n"]
    for a in assets:
        status = "✅" if a.get("is_active") else "⏸️"
        lines.append(f"{status} `{a['market']}` ({a.get('intent', '')})")
    await send_message(token, chat_id, "\n".join(lines))


async def cmd_add_asset(token: str, chat_id: str, market: str) -> None:
    if not market or "-" not in market:
        await send_message(token, chat_id, "❌ 형식: /add KRW-BTC")
        return

    assets = await get_active_markets()
    if any(a["market"] == market for a in assets):
        await send_message(token, chat_id, f"⚠️ `{market}` 이미 추적 중입니다.")
        return

    await add_tracked_asset(market, intent="watch")

    backfill_msg = ""
    try:
        for tf in _BACKFILL_TFS:
            await push_trade_job({
                "type": "collect_candles",
                "payload": {"market": market, "timeframe": tf},
            })
        backfill_msg = "\n📊 백필 Job 생성 완료 (4개 TF)"
    except Exception:
        backfill_msg = "\n⚠️ 백필 Job 생성 실패 (다음 주기에 수집)"

    await send_message(
        token, chat_id, f"✅ `{market}` 추적 자산에 추가되었습니다.{backfill_msg}",
    )


async def cmd_remove_asset(token: str, chat_id: str, market: str) -> None:
    if not market:
        await send_message(token, chat_id, "❌ 형식: /remove KRW-BTC")
        return

    assets = await get_active_markets()
    if not any(a["market"] == market for a in assets):
        await send_message(token, chat_id, f"⚠️ `{market}` 은 추적 목록에 없습니다.")
        return

    await update_tracked_asset(market, is_active=False)
    await send_message(token, chat_id, f"🗑️ `{market}` 추적을 중단했습니다.")

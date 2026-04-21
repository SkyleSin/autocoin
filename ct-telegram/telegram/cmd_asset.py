"""텔레그램 자산 관리 명령어 핸들러."""

from __future__ import annotations

import logging

from infra.api.tracked_asset import add_tracked_asset, get_active_markets
from infra.api.user import get_user_watchlist, update_user_watchlist
from infra.redis_queue import push_trade_job

from telegram.bot import send_message

logger = logging.getLogger(__name__)

_BACKFILL_TFS = ["15m", "1h", "4h", "1d"]


async def cmd_watchlist(token: str, chat_id: str, user_id: int) -> None:
    """유저 watchlist 조회."""
    watchlist = await get_user_watchlist(user_id)
    if not watchlist:
        await send_message(
            token, chat_id, "📋 워치리스트가 비어있습니다.\n/add KRW-BTC 로 추가하세요.",
        )
        return

    assets = await get_active_markets()
    price_map = {a["market"]: a.get("current_price") for a in assets}

    lines = ["📋 *워치리스트*\n"]
    for market in watchlist:
        price = price_map.get(market)
        if price:
            lines.append(f"• `{market}` — {price:,.0f}원")
        else:
            lines.append(f"• `{market}`")
    await send_message(token, chat_id, "\n".join(lines))


async def cmd_add_asset(token: str, chat_id: str, user_id: int, market: str) -> None:
    """유저 watchlist에 추가 + tracked_assets에 없으면 생성."""
    if not market or "-" not in market:
        await send_message(token, chat_id, "❌ 형식: /add KRW-BTC")
        return

    watchlist = await get_user_watchlist(user_id)
    if market in watchlist:
        await send_message(token, chat_id, f"⚠️ `{market}` 이미 워치리스트에 있습니다.")
        return

    # tracked_assets에 없으면 추가 (수집 보장)
    assets = await get_active_markets()
    if not any(a["market"] == market for a in assets):
        await add_tracked_asset(market, intent="watch")
        # 백필
        try:
            for tf in _BACKFILL_TFS:
                await push_trade_job({
                    "type": "collect_candles",
                    "payload": {"market": market, "timeframe": tf},
                })
        except Exception:
            logger.warning("백필 Job 생성 실패: %s", market)

    # 유저 watchlist 업데이트
    watchlist.append(market)
    await update_user_watchlist(user_id, watchlist)
    await send_message(token, chat_id, f"✅ `{market}` 워치리스트에 추가되었습니다.")


async def cmd_remove_asset(token: str, chat_id: str, user_id: int, market: str) -> None:
    """유저 watchlist에서 제거 (tracked_assets는 유지)."""
    if not market:
        await send_message(token, chat_id, "❌ 형식: /rm KRW-BTC")
        return

    watchlist = await get_user_watchlist(user_id)
    if market not in watchlist:
        await send_message(token, chat_id, f"⚠️ `{market}` 은 워치리스트에 없습니다.")
        return

    watchlist.remove(market)
    await update_user_watchlist(user_id, watchlist)
    await send_message(token, chat_id, f"🗑️ `{market}` 워치리스트에서 제거했습니다.")

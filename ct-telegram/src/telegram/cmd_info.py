"""텔레그램 조회 명령어 핸들러 (잔고/마켓/이력/상태/리포트)."""

from __future__ import annotations

import logging

from infra.api.report import generate_detail_report, generate_report
from infra.api.signal import get_latest_signal
from infra.api.tracked_asset import get_active_markets
from infra.api.trade import get_trades
from telegram.bot import send_message
from utils.config import Settings

logger = logging.getLogger(__name__)


async def _cmd_balance(token: str, chat_id: str, settings: Settings) -> None:
    """잔고 조회."""
    try:
        import asyncio

        from job_runner.workers import PrivateRequester

        def _fetch(s: Settings) -> list[dict]:
            with PrivateRequester(s) as prv:
                return prv.request("GET", "/v1/accounts")

        accounts = await asyncio.to_thread(_fetch, settings)
        lines = ["💰 *잔고 현황*\n"]
        for acc in accounts:
            currency = acc.get("currency", "?")
            balance = float(acc.get("balance", 0))
            locked = float(acc.get("locked", 0))
            if balance > 0 or locked > 0:
                lines.append(f"`{currency}`: {balance:,.4f} (묶임: {locked:,.4f})")
        await send_message(token, chat_id, "\n".join(lines))
    except Exception as e:
        await send_message(token, chat_id, f"❌ 잔고 조회 실패: {e}")


async def _cmd_markets(
    token: str, chat_id: str, settings: Settings, keyword: str = "",
) -> None:
    """빗썸 KRW 마켓 목록 조회."""
    try:
        import asyncio

        from job_runner.workers import PublicRequester

        def _fetch(s: Settings) -> list[dict]:
            with PublicRequester(s) as pub:
                return pub.request("GET", "/v1/market/all")

        markets = await asyncio.to_thread(_fetch, settings)
        krw = [m for m in markets if str(m.get("market", "")).startswith("KRW-")]

        if keyword:
            krw = [
                m for m in krw
                if m.get("market", "").replace("KRW-", "").startswith(keyword)
                or keyword in m.get("english_name", "").upper()
                or keyword in m.get("korean_name", "")
            ]
        krw.sort(key=lambda m: m["market"])

        if not krw:
            await send_message(
                token, chat_id,
                f"검색 결과 없음: `{keyword}`\n사용법: `/markets` 또는 `/markets BTC`",
            )
            return

        header = f"📋 *빗썸 KRW 마켓* ({len(krw)}개)"
        if keyword:
            header = f"📋 *빗썸 KRW 마켓* (검색: {keyword}, {len(krw)}개)"
        lines = [header + "\n"]
        for m in krw:
            lines.append(
                f"`{m['market']}` — {m.get('korean_name', '')} "
                f"({m.get('english_name', '')})",
            )

        text = "\n".join(lines)
        if len(text) > 4000:
            text = "\n".join(lines[:70])
            text += f"\n\n... 외 {len(krw) - 69}개\n`/markets 키워드` 로 검색하세요"
        await send_message(token, chat_id, text)
    except Exception as e:
        await send_message(token, chat_id, f"❌ 마켓 조회 실패: {e}")


async def _cmd_history(token: str, chat_id: str) -> None:
    """최근 매매 이력."""
    trades = await get_trades(limit=10)
    if not trades:
        await send_message(token, chat_id, "📜 매매 이력이 없습니다.")
        return

    lines = ["📜 *최근 매매 이력*\n"]
    for t in trades:
        side_emoji = "🟢" if t.get("side") == "buy" else "🔴"
        created = t.get("created_at", "")[:16].replace("T", " ")
        lines.append(
            f"{side_emoji} `{t['market']}` {t['side']} "
            f"v={t['volume']} p={t['price_krw']} "
            f"[{t['status']}] {created}",
        )
    await send_message(token, chat_id, "\n".join(lines))

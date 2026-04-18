"""텔레그램 조회 명령어 핸들러."""

from __future__ import annotations

import logging

from infra.api.report import generate_report
from infra.api.signal import get_latest_signal
from infra.api.tracked_asset import get_active_markets
from infra.api.trade import get_trades
from telegram.bot import send_message
from utils.config import Settings

logger = logging.getLogger(__name__)


async def cmd_history(token: str, chat_id: str) -> None:
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


async def cmd_status(token: str, chat_id: str, settings: Settings) -> None:
    """시스템 상태 — 추적 자산 + 최신 시그널."""
    assets = await get_active_markets()
    if not assets:
        await send_message(token, chat_id, "📊 추적 중인 자산이 없습니다.")
        return

    lines = ["📊 *시스템 상태*\n"]
    for a in assets:
        market = a["market"]
        sig = await get_latest_signal(market)
        if sig:
            sig_type = sig.get("signal_type", "?")
            conf = sig.get("confidence", 0)
            lines.append(f"`{market}` — {sig_type} ({conf:.0%})")
        else:
            lines.append(f"`{market}` — 시그널 없음")
    await send_message(token, chat_id, "\n".join(lines))


async def cmd_report(token: str, chat_id: str, settings: Settings) -> None:
    """리포트 생성 + 전송."""
    try:
        result = await generate_report()
        text = result.get("text") or result.get("report", "리포트 생성 완료")
        await send_message(token, chat_id, text)
    except Exception as e:
        await send_message(token, chat_id, f"❌ 리포트 생성 실패: {e}")


# TODO: /balance, /markets — ct-worker 빗썸 API 연동 필요

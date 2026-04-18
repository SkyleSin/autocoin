"""텔레그램 조회 명령어 핸들러."""

from __future__ import annotations

import logging

from infra.api.market import get_markets
from infra.api.portfolio import get_user_portfolio
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



async def cmd_balance(token: str, chat_id: str, user_id: int) -> None:
    """유저 보유 자산 현황."""
    portfolio = await get_user_portfolio(user_id)
    if not portfolio:
        await send_message(token, chat_id, "💰 보유 자산이 없습니다.")
        return

    lines = ["💰 *보유 자산*\n"]
    for item in portfolio:
        currency = item["currency"]
        balance = float(item["balance"])
        locked = float(item.get("locked", 0))
        avg_price = float(item.get("avg_buy_price", 0))

        if currency == "KRW":
            lines.append(f"`KRW`: {balance:,.0f}원 (묶임: {locked:,.0f})")
        else:
            line = f"`{currency}`: {balance:,.8f}"
            if locked > 0:
                line += f" (묶임: {locked:,.8f})"
            if avg_price > 0:
                line += f" (평균가: {avg_price:,.0f})"
            lines.append(line)

    await send_message(token, chat_id, "\n".join(lines))


async def cmd_markets(token: str, chat_id: str, keyword: str = "") -> None:
    """빗썸 KRW 마켓 목록."""
    markets = await get_markets(keyword)
    if not markets:
        text = f"검색 결과 없음: `{keyword}`" if keyword else "마켓 목록이 비어있습니다.\n(서버 시작 후 동기화 대기 중)"
        await send_message(token, chat_id, text)
        return

    header = f"📋 *KRW 마켓* ({len(markets)}개)"
    if keyword:
        header = f"📋 *KRW 마켓* (검색: {keyword}, {len(markets)}개)"

    lines = [header + "\n"]
    for m in markets[:50]:
        lines.append(
            f"`{m['market']}` — {m.get('korean_name', '')} "
            f"({m.get('english_name', '')})",
        )
    if len(markets) > 50:
        lines.append(f"\n... 외 {len(markets) - 50}개")

    await send_message(token, chat_id, "\n".join(lines))

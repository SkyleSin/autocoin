"""마스터 봇 명령어 — 시스템 모니터링 + 지표 리포트."""

from __future__ import annotations

import logging

from infra.api.indicator_snapshot import get_latest_snapshot
from infra.api.signal import get_recent_signals, get_signal_summary
from infra.api.tracked_asset import get_active_markets
from infra.api.user import get_active_users

from telegram.bot import send_message

logger = logging.getLogger(__name__)

# ── 백테스트 실측 적중률 (topdown_tables.py 기반) ──
_ACCURACY_TABLE = {
    "4H BUY": [
        ("macd_골든크로스", 74.5, 204),
        ("bb_squeeze", 74.1, 174),
        ("price>sma20", 72.6, 413),
        ("macd_hist_증가중", 72.4, 492),
        ("sr_지지근접", 71.1, 232),
        ("price>sma50", 71.0, 497),
        ("sr_지지반등", 70.7, 205),
        ("ema12>ema26", 70.3, 609),
        ("sr_저항돌파", 68.9, 148),
    ],
    "4H SELL": [
        ("macd_hist_음수", 67.3, 1360),
        ("rsi<40", 67.1, 1275),
        ("price<sma50", 66.7, 1553),
        ("sr_저항거부", 66.7, 87),
        ("price<sma20", 66.5, 1637),
        ("macd_hist_감소중", 66.3, 1558),
        ("macd_데드크로스", 65.4, 315),
    ],
    "1H BUY": [
        ("sr_지지반등", 83.6, 67),
        ("sr_저항돌파", 78.4, 74),
    ],
    "1H SELL": [
        ("sr_저항거부", 68.6, 35),
    ],
}


async def cmd_sys(token: str, chat_id: str) -> None:
    """시스템 상태 — 추적 종목, 시그널 현황, 유저 수."""
    assets = await get_active_markets()
    summary = await get_signal_summary(hours=24)
    users = await get_active_users()

    # 최근 시그널 시간
    recent = await get_recent_signals(limit=1)
    last_signal_time = "-"
    if recent:
        last_signal_time = recent[0].get("created_at", "")[:16].replace("T", " ")

    lines = [
        "🖥 *시스템 상태*\n",
        f"📋 추적 종목: {len(assets)}개",
        f"👥 활성 유저: {len(users)}명",
        "\n📊 *24h 시그널:*",
        f"  총 {summary['total']}건"
        f" (BUY {summary['buy']} / SELL {summary['sell']} / HOLD {summary['hold']})",
        f"  마지막 시그널: {last_signal_time}",
    ]

    # 추적 종목 현재가 요약
    priced = [a for a in assets if a.get("current_price")]
    if priced:
        updated = priced[0].get("price_updated_at", "")[:16].replace("T", " ")
        lines.append(f"\n💹 현재가 갱신: {updated}")

    await send_message(token, chat_id, "\n".join(lines))


async def cmd_sig(token: str, chat_id: str) -> None:
    """시그널 요약 + 최근 20건."""
    summary = await get_signal_summary(hours=24)
    recent = await get_recent_signals(limit=20)

    lines = [
        "📊 *시그널 리포트*\n",
        f"*24h 요약:* 총 {summary['total']}건"
        f" (BUY {summary['buy']} / SELL {summary['sell']} / HOLD {summary['hold']})",
    ]

    if not recent:
        lines.append("\n최근 BUY/SELL 시그널 없음")
    else:
        lines.append(f"\n*최근 {len(recent)}건:*")
        for s in recent:
            market = s.get("market", "").replace("KRW-", "")
            sig_type = s.get("signal_type", "?")
            conf = s.get("confidence", 0)
            source = s.get("source", "?")
            created = s.get("created_at", "")[:16].replace("T", " ")

            emoji = "🟢" if sig_type == "BUY" else "🔴"
            src_tag = {"technical": "TD", "deepseek": "DS", "local_llm": "OL"}.get(
                source, source[:2].upper()
            )
            lines.append(
                f"{emoji} `{market}` {sig_type} {conf:.0%} [{src_tag}] {created}"
            )

    await send_message(token, chat_id, "\n".join(lines))


async def cmd_ind(token: str, chat_id: str) -> None:
    """지표별 적중률 테이블 + 현재 지표 스냅샷."""
    # 1. 백테스트 적중률 테이블
    lines = ["📈 *지표별 적중률* (BTC+ETH 백테스트 2050건)\n"]

    for section, items in _ACCURACY_TABLE.items():
        lines.append(f"*{section}:*")
        for name, acc, cnt in items:
            bar = "█" * int(acc / 10) + "░" * (10 - int(acc / 10))
            lines.append(f"  `{name:14s}` {bar} {acc:.1f}% ({cnt}건)")
        lines.append("")

    # 2. 현재 지표 스냅샷
    assets = await get_active_markets()
    markets = [a["market"] for a in assets if a.get("market")]

    if markets:
        lines.append("*현재 지표 (15m):*")
        for market in markets[:10]:
            snap = await get_latest_snapshot(market, "15m")
            if not snap:
                continue
            coin = market.replace("KRW-", "")
            rsi = snap.get("rsi_14")
            macd_h = snap.get("macd_histogram")
            vol = snap.get("volume_ratio")
            bb_w = snap.get("bb_width")

            parts = [f"`{coin:5s}`"]
            if rsi is not None:
                parts.append(f"RSI {float(rsi):.1f}")
            if macd_h is not None:
                parts.append(f"MACD {float(macd_h):+.0f}")
            if vol is not None:
                parts.append(f"Vol {float(vol):.2f}x")
            if bb_w is not None:
                parts.append(f"BB {float(bb_w):.3f}")
            lines.append("  " + " | ".join(parts))

    await send_message(token, chat_id, "\n".join(lines))


async def cmd_tracked(token: str, chat_id: str) -> None:
    """전체 추적 자산 + 현재가."""
    assets = await get_active_markets()
    if not assets:
        await send_message(token, chat_id, "추적 중인 자산이 없습니다.")
        return

    lines = [f"📋 *추적 자산* ({len(assets)}개)\n"]
    for a in assets:
        market = a["market"]
        price = a.get("current_price")
        intent = a.get("intent", "")
        if price:
            lines.append(f"• `{market}` — {price:,.0f}원 [{intent}]")
        else:
            lines.append(f"• `{market}` — 가격 없음 [{intent}]")

    updated = None
    for a in assets:
        if a.get("price_updated_at"):
            updated = a["price_updated_at"][:16].replace("T", " ")
            break
    if updated:
        lines.append(f"\n_갱신: {updated}_")

    await send_message(token, chat_id, "\n".join(lines))


async def cmd_users(token: str, chat_id: str) -> None:
    """유저 목록."""
    users = await get_active_users()
    if not users:
        await send_message(token, chat_id, "등록된 유저가 없습니다.")
        return

    lines = [f"👥 *유저 목록* ({len(users)}명)\n"]
    for u in users:
        status = "✅" if u.get("is_active") else "⏸️"
        watchlist = u.get("coin_watchlist", [])
        wl_count = len(watchlist) if watchlist else 0
        lines.append(
            f"{status} `{u['username']}` (id:{u['id']}) — watchlist {wl_count}개"
        )

    await send_message(token, chat_id, "\n".join(lines))

"""수익률 분석 리포트 생성."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.signal import Signal
from models.trade import Trade
from services.accuracy_service import get_accuracy_summary, verify_pending_signals

logger = logging.getLogger(__name__)

# 패턴 영문→한글 매핑
_PATTERN_KO = {
    "double_top": "쌍봉",
    "double_bottom": "쌍바닥",
    "triple_top": "삼중천정",
    "triple_bottom": "트리플바닥",
    "head_and_shoulders": "머리어깨",
    "inverse_head_and_shoulders": "역머리어깨",
    "ascending_triangle": "상승삼각형",
    "descending_triangle": "하락삼각형",
    "symmetric_triangle": "대칭삼각형",
    "rising_wedge": "상승쐐기형",
    "falling_wedge": "하락쐐기형",
    "bull_flag": "상승깃발",
    "bear_flag": "하락깃발",
    "box_range": "박스권횡보",
}


async def generate_report(
    session: AsyncSession,
    accounts: list[dict] | None = None,
    ticker_prices: dict[str, float] | None = None,
) -> str:
    """수익률 + 적중률 리포트 텍스트 생성.

    Args:
        accounts: 빗썸 잔고 (없으면 잔고 섹션 생략)
        ticker_prices: {market: current_price} (없으면 수익률 생략)
    """
    now = datetime.now(UTC)
    kst_now = now + timedelta(hours=9)
    lines = [f"📊 *수익률 리포트* ({kst_now:%Y-%m-%d %H:%M} KST)\n"]

    # ── 1. 보유 자산 ──
    if accounts:
        lines.append("💰 *보유 자산*")
        for acc in accounts:
            currency = acc.get("currency", "")
            balance = float(acc.get("balance", 0))
            if balance <= 0:
                continue
            if currency == "KRW":
                lines.append(f"• KRW 잔고: `{balance:,.0f}원`")
            else:
                market = f"KRW-{currency}"
                avg_price = float(acc.get("avg_buy_price", 0))
                cur_price = (ticker_prices or {}).get(market, 0)
                if avg_price > 0 and cur_price > 0:
                    pnl_pct = (cur_price - avg_price) / avg_price * 100
                    pnl_emoji = "📈" if pnl_pct >= 0 else "📉"
                    lines.append(
                        f"• {market}: `{balance:.4f}` {currency} | "
                        f"{pnl_emoji} `{pnl_pct:+.1f}%` "
                        f"(평균 {avg_price:,.0f} → 현재 {cur_price:,.0f})"
                    )
                elif balance > 0:
                    lines.append(f"• {market}: `{balance:.4f}` {currency}")
        lines.append("")

    # ── 2. 일일 매매 요약 ──
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_trades = (await session.execute(
        select(Trade).where(Trade.created_at >= today_start)
        .order_by(Trade.created_at.desc())
    )).scalars().all()

    if daily_trades:
        lines.append(f"📋 *일일 매매* ({len(daily_trades)}건)")
        for t in daily_trades[:5]:
            side_emoji = "🟢" if t.side == "buy" else "🔴"
            lines.append(
                f"• {side_emoji} {t.market} {t.side} "
                f"`{float(t.volume):.4f}` @ `{float(t.price_krw or 0):,.0f}`"
            )
        if len(daily_trades) > 5:
            lines.append(f"  ... 외 {len(daily_trades) - 5}건")
        lines.append("")
    else:
        lines.append("📋 *일일 매매*: 없음\n")

    # ── 3. 적중률 검증 (미검증 시그널 처리) ──
    verify_result = await verify_pending_signals(session)
    if verify_result["verified"] > 0:
        lines.append(
            f"🔄 *신규 검증*: {verify_result['verified']}건 "
            f"(적중 {verify_result['hit']}건)\n"
        )

    # ── 4. 패턴 적중률 (누적) ──
    accuracy = await get_accuracy_summary(session)
    if accuracy:
        lines.append("🎯 *패턴 적중률 (누적)*")
        total_all = sum(a["total"] for a in accuracy)
        hit_all = sum(a["hit"] for a in accuracy)
        for a in accuracy:
            ko = _PATTERN_KO.get(a["pattern"], a["pattern"])
            lines.append(
                f"• {ko}: `{a['accuracy']:.0f}%` ({a['hit']}/{a['total']})"
            )
        if total_all > 0:
            overall = hit_all / total_all * 100
            lines.append(f"• *전체*: `{overall:.0f}%` ({hit_all}/{total_all})")
        lines.append("")

    # ── 5. 최근 시그널 현황 ──
    recent_signals = (await session.execute(
        select(Signal).where(
            Signal.source == "local_llm",
            Signal.signal_type.in_(["BUY", "SELL"]),
            Signal.created_at >= now - timedelta(hours=24),
        ).order_by(Signal.created_at.desc()).limit(10)
    )).scalars().all()

    if recent_signals:
        lines.append("📡 *최근 24h 시그널*")
        for s in recent_signals[:5]:
            kst = s.created_at + timedelta(hours=9)
            emoji = "🟢" if s.signal_type == "BUY" else "🔴"
            verified_mark = "✅" if s.verified else "⏳"
            lines.append(
                f"• {emoji} {s.market} {s.signal_type} "
                f"({kst:%H:%M}) {verified_mark}"
            )
        lines.append("")

    return "\n".join(lines)

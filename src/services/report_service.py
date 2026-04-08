"""수익률 분석 리포트 생성."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.patterns import _ACTIVE_PATTERNS
from models.candle import Candle
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
            Signal.source.in_(["deepseek", "local_llm"]),
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
            src_label = "[DS]" if s.source == "deepseek" else "[OL]"
            lines.append(
                f"• {emoji} {s.market} {s.signal_type} "
                f"({kst:%H:%M}) {src_label} {verified_mark}"
            )
        lines.append("")

    return "\n".join(lines)


async def generate_detail_report(session: AsyncSession) -> str:
    """LLM 판단 상세 리포트 — 실현 수익률 + 판단 이력 테이블."""
    now = datetime.now(UTC)
    kst_now = now + timedelta(hours=9)
    lines = [f"🔍 *상세 리포트* ({kst_now:%m/%d %H:%M} KST)\n"]

    # ── 1. 실현 수익률 (매매 기록 기반) ──
    trades = (await session.execute(
        select(Trade).order_by(Trade.created_at.desc()).limit(20)
    )).scalars().all()

    buy_trades: dict[str, Trade] = {}
    realized: list[dict] = []

    # 매수→매도 페어링 (최근 것부터)
    for t in reversed(list(trades)):
        if t.side == "buy":
            buy_trades[t.market] = t
        elif t.side == "sell" and t.market in buy_trades:
            bt = buy_trades.pop(t.market)
            buy_price = float(bt.price_krw or 0)
            sell_price = float(t.price_krw or 0)
            if buy_price > 0 and sell_price > 0:
                pnl_pct = (sell_price - buy_price) / buy_price * 100
                realized.append({
                    "market": t.market,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "pnl_pct": pnl_pct,
                    "sold_at": t.created_at,
                })

    if realized:
        wins = sum(1 for r in realized if r["pnl_pct"] > 0)
        losses = sum(1 for r in realized if r["pnl_pct"] <= 0)
        avg_pnl = sum(r["pnl_pct"] for r in realized) / len(realized)

        lines.append(f"💰 *실현 수익률* (승 {wins} / 패 {losses})")
        lines.append("```")
        lines.append(f"{'마켓':<10} {'매수가':>10} {'매도가':>10} {'수익률':>7}")
        lines.append("-" * 40)
        for r in realized[-8:]:
            coin = r["market"].replace("KRW-", "")
            pnl_str = f"{r['pnl_pct']:+.1f}%"
            lines.append(
                f"{coin:<10} {r['buy_price']:>10,.0f} {r['sell_price']:>10,.0f} {pnl_str:>7}"
            )
        lines.append(f"\n평균: {avg_pnl:+.2f}%")
        lines.append("```")
    else:
        lines.append("💰 *실현 수익률*: 매매 기록 없음\n")

    # ── 2. 최근 LLM 판단 이력 (테이블) ──
    recent_llm = (await session.execute(text("""
        SELECT
            to_char(s.created_at AT TIME ZONE 'Asia/Seoul', 'MM/DD HH24:MI') as kst,
            REPLACE(s.market, 'KRW-', '') as coin,
            COALESCE(s.indicators->'patterns'->'patterns'->0->>'name_ko', '-') as pattern,
            s.signal_type as decision,
            s.confidence as conf,
            sp.signal_price,
            fp.future_price,
            s.indicators->>'rsi_14' as rsi,
            s.indicators->>'macd_histogram' as macd_h,
            s.indicators->'support_resistance'->>'signal' as sr_sig
        FROM signals s
        LEFT JOIN LATERAL (
            SELECT c.close as signal_price FROM candles c
            WHERE c.market = s.market AND c.timeframe = '15m' AND c.open_at <= s.created_at
            ORDER BY c.open_at DESC LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT c.close as future_price FROM candles c
            WHERE c.market = s.market AND c.timeframe = '15m'
            AND c.open_at >= s.created_at + interval '4 hours'
            ORDER BY c.open_at ASC LIMIT 1
        ) fp ON true
        WHERE s.source IN ('deepseek', 'local_llm')
          AND s.signal_type IN ('BUY', 'SELL')
        ORDER BY s.created_at ASC
        LIMIT 20
    """))).fetchall()

    if recent_llm:
        lines.append("📋 *최근 LLM 판단*")
        lines.append("```")
        lines.append(f"{'시간':>11} {'코인':<4} {'패턴':<5} {'판단':<4} {'RSI':>4} {'4h후':>6} {'결과':>2}")
        lines.append("-" * 42)
        for row in recent_llm:
            kst, coin, pattern, decision, conf, sig_price, fut_price, rsi_raw, macd_h, sr_sig = row
            pat_short = (pattern or "-")[:5]
            rsi_str = f"{float(rsi_raw):.0f}" if rsi_raw else "-"

            if fut_price and sig_price and float(sig_price) > 0:
                chg = (float(fut_price) - float(sig_price)) / float(sig_price) * 100
                chg_str = f"{chg:+.1f}%"
                # 판단이 맞았는지
                if decision == "BUY":
                    mark = "O" if chg > 0 else "X"
                else:
                    mark = "O" if chg < 0 else "X"
            else:
                chg_str = " 대기"
                mark = "-"

            lines.append(
                f"{kst:>11} {coin:<4} {pat_short:<5} {decision:<4}"
                f" {rsi_str:>4} {chg_str:>6} {mark:>2}"
            )
        lines.append("```")

        # 지표 범례
        sr_map = {
            "SUPPORT_BOUNCE": "지지반등",
            "SUPPORT_BREAK": "지지이탈",
            "RESISTANCE_BREAK": "저항돌파",
            "RESISTANCE_REJECT": "저항저항",
        }
        lines.append("_결과: O=판단 적중 X=실패 -=대기_")
        lines.append("_4h 후 변동은 참고용. 실현 수익률이 실제 성과._\n")

    # ── 3. 패턴별 적중률 (테이블 기반 집계) ──
    pattern_accuracy = (await session.execute(text("""
        WITH pat_eval AS (
            SELECT
                COALESCE(s.indicators->'patterns'->'patterns'->0->>'name_ko', '패턴없음') as pattern,
                s.signal_type,
                sp.signal_price, fp.future_price,
                CASE
                    WHEN fp.future_price IS NULL THEN NULL
                    WHEN s.signal_type = 'BUY' AND fp.future_price > sp.signal_price THEN true
                    WHEN s.signal_type = 'SELL' AND fp.future_price < sp.signal_price THEN true
                    ELSE false
                END as correct
            FROM signals s
            LEFT JOIN LATERAL (
                SELECT c.close as signal_price FROM candles c
                WHERE c.market = s.market AND c.timeframe = '15m' AND c.open_at <= s.created_at
                ORDER BY c.open_at DESC LIMIT 1
            ) sp ON true
            LEFT JOIN LATERAL (
                SELECT c.close as future_price FROM candles c
                WHERE c.market = s.market AND c.timeframe = '15m'
                AND c.open_at >= s.created_at + interval '4 hours'
                ORDER BY c.open_at ASC LIMIT 1
            ) fp ON true
            WHERE s.source IN ('deepseek', 'local_llm')
              AND s.signal_type IN ('BUY', 'SELL')
        )
        SELECT pattern, signal_type,
            count(*) FILTER (WHERE correct IS NOT NULL) as evaluated,
            count(*) FILTER (WHERE correct = true) as hit
        FROM pat_eval
        WHERE pattern IS NOT NULL
        GROUP BY pattern, signal_type
        ORDER BY evaluated DESC, pattern
    """))).fetchall()

    if pattern_accuracy:
        lines.append("📐 *패턴별 적중률* (참고: 4h 기준)")
        lines.append("```")
        lines.append(f"{'패턴':<6} {'판단':<4} {'적중':>4} {'전체':>4} {'적중률':>5}")
        lines.append("-" * 28)
        for row in pattern_accuracy:
            pat, sig, evaluated, hit = row
            rate = f"{hit / evaluated * 100:.0f}%" if evaluated > 0 else "N/A"
            lines.append(f"{pat[:6]:<6} {sig:<4} {hit:>4} {evaluated:>4} {rate:>5}")
        lines.append("```\n")

    # ── 4-1. 관찰 중 패턴 (비활성) ──
    inactive_accuracy = await _get_inactive_pattern_accuracy(session)
    if inactive_accuracy:
        lines.append("🔬 *관찰 중 패턴 (비활성)*")
        lines.append("```")
        lines.append(f"{'패턴':<6} {'감지':>4} {'적중':>4} {'적중률':>5}")
        lines.append("-" * 24)
        for row in inactive_accuracy:
            ko = _PATTERN_KO.get(row["pattern"], row["pattern"])
            rate = (
                f"{row['accuracy']:.0f}%"
                if row["total"] > 0 else "N/A"
            )
            lines.append(
                f"{ko[:6]:<6} {row['total']:>4}"
                f" {row['hit']:>4} {rate:>5}"
            )
        lines.append("```")
        lines.append("_활성화 기준: 적중률 50%+_\n")

    # ── 5. HOLD 판단 요약 ──
    hold_stats = (await session.execute(text("""
        SELECT
            count(*) as total,
            count(*) FILTER (WHERE abs((fp.future_price - sp.signal_price)
                / sp.signal_price * 100) < 2.0) as stable
        FROM signals s
        LEFT JOIN LATERAL (
            SELECT c.close as signal_price FROM candles c
            WHERE c.market = s.market AND c.timeframe = '15m' AND c.open_at <= s.created_at
            ORDER BY c.open_at DESC LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT c.close as future_price FROM candles c
            WHERE c.market = s.market AND c.timeframe = '15m'
            AND c.open_at >= s.created_at + interval '4 hours'
            ORDER BY c.open_at ASC LIMIT 1
        ) fp ON true
        WHERE s.source IN ('deepseek', 'local_llm')
          AND s.signal_type = 'HOLD'
          AND fp.future_price IS NOT NULL
          AND sp.signal_price > 0
    """))).fetchone()

    if hold_stats and hold_stats[0] > 0:
        total, stable = hold_stats
        lines.append(
            f"⏸️ *HOLD 판단*: {total}건 중 {stable}건 안정"
            f" ({stable/total*100:.0f}% 가 4h 내 2% 이내)"
        )

    # ── 6. 시그널 현황 ──
    total_counts = (await session.execute(text("""
        SELECT
            count(*) FILTER (WHERE source = 'technical') as tech,
            count(*) FILTER (WHERE source IN ('deepseek', 'local_llm')) as llm,
            count(*) FILTER (WHERE source IN ('deepseek', 'local_llm')
                AND signal_type IN ('BUY', 'SELL')) as llm_bs,
            count(*) FILTER (WHERE source IN ('deepseek', 'local_llm')
                AND signal_type = 'HOLD') as llm_hold
        FROM signals
    """))).fetchone()

    if total_counts:
        tech, llm, llm_bs, llm_hold = total_counts
        lines.append(
            f"\n📊 기술분석 `{tech}건` → LLM `{llm}건`"
            f" (BUY/SELL `{llm_bs}` HOLD `{llm_hold}`)"
        )

    return "\n".join(lines)


async def _get_inactive_pattern_accuracy(
    session: AsyncSession,
) -> list[dict]:
    """비활성 패턴의 적중률을 pattern_accuracies 테이블에서 조회."""
    from models.pattern_accuracy import PatternAccuracy

    rows = (await session.execute(
        select(PatternAccuracy)
        .where(PatternAccuracy.total_count > 0)
        .order_by(PatternAccuracy.total_count.desc())
    )).scalars().all()
    return [
        {
            "pattern": r.pattern_name,
            "total": r.total_count,
            "hit": r.hit_count,
            "accuracy": r.accuracy_pct,
        }
        for r in rows
        if r.pattern_name not in _ACTIVE_PATTERNS
    ]

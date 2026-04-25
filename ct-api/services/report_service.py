"""수익률 분석 리포트 생성."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from models.signal import Signal
from models.trade import Trade
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

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

_SR_KO = {
    "SUPPORT_BOUNCE": "지지 반등",
    "SUPPORT_BREAK": "지지 이탈",
    "RESISTANCE_BREAK": "저항 돌파",
    "RESISTANCE_REJECT": "저항 저항",
}

_SRC_LABELS = {
    "technical": "탑다운",
    "deepseek": "DeepSeek R1",
    "local_llm": "Ollama",
}

_SRC_SHORT = {"technical": "[TD]", "deepseek": "[DS]", "local_llm": "[OL]"}


# ──────────────────────────────────────────────
# generate_report 섹션 헬퍼
# ──────────────────────────────────────────────


def _section_holdings(
    accounts: list[dict] | None,
    ticker_prices: dict[str, float] | None,
) -> list[str]:
    """보유 자산 섹션."""
    if not accounts:
        return []
    lines = ["💰 *보유 자산*"]
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
    return lines


async def _section_daily_trades(session: AsyncSession, now: datetime) -> list[str]:
    """일일 매매 섹션."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_trades = (await session.execute(
        select(Trade).where(Trade.created_at >= today_start)
        .order_by(Trade.created_at.desc())
    )).scalars().all()

    if not daily_trades:
        return ["📋 *일일 매매*: 없음\n"]

    lines = [f"📋 *일일 매매* ({len(daily_trades)}건)"]
    for t in daily_trades[:5]:
        side_emoji = "🟢" if t.side == "buy" else "🔴"
        lines.append(
            f"• {side_emoji} {t.market} {t.side} "
            f"`{float(t.volume):.4f}` @ `{float(t.price_krw or 0):,.0f}`"
        )
    if len(daily_trades) > 5:
        lines.append(f"  ... 외 {len(daily_trades) - 5}건")
    lines.append("")
    return lines


async def _section_topdown_indicators(session: AsyncSession) -> list[str]:
    """탑다운 활성 지표 섹션."""
    try:
        from core.strategy.topdown_tables import (
            BUY_1H,
            BUY_4H,
            SELL_1H,
            SELL_4H,
            TRIGGER_BUY_15M,
            TRIGGER_SELL_15M,
        )
    except Exception:
        return []

    lines = ["📊 *탑다운 활성 지표*"]
    lines.append(f"  4H BUY: {len(BUY_4H)}개 | SELL: {len(SELL_4H)}개")
    lines.append(f"  1H BUY: {len(BUY_1H)}개 | SELL: {len(SELL_1H)}개")
    lines.append(
        f"  15m 트리거: BUY {len(TRIGGER_BUY_15M)}개"
        f" | SELL {len(TRIGGER_SELL_15M)}개"
    )
    top3_buy = ", ".join(f"{n}({s:.2f})" for n, s, _ in BUY_4H[:3])
    top3_sell = ", ".join(f"{n}({s:.2f})" for n, s, _ in SELL_4H[:3])
    lines.append(f"  Top3 BUY: {top3_buy}")
    lines.append(f"  Top3 SELL: {top3_sell}")
    lines.append("")
    return lines


async def _section_recent_signals(
    session: AsyncSession, now: datetime,
) -> list[str]:
    """최근 24h 시그널 섹션."""
    recent_signals = (await session.execute(
        select(Signal).where(
            Signal.source.in_(["technical", "deepseek", "local_llm"]),
            Signal.signal_type.in_(["BUY", "SELL"]),
            Signal.created_at >= now - timedelta(hours=24),
        ).order_by(Signal.created_at.desc()).limit(10)
    )).scalars().all()

    if not recent_signals:
        return []

    lines = ["📡 *최근 24h 시그널*"]
    for s in recent_signals[:5]:
        kst = s.created_at + timedelta(hours=9)
        emoji = "🟢" if s.signal_type == "BUY" else "🔴"
        verified_mark = "✅" if s.verified else "⏳"
        src_label = _SRC_SHORT.get(s.source, f"[{s.source}]")
        lines.append(
            f"• {emoji} {s.market} {s.signal_type} "
            f"({kst:%H:%M}) {src_label} {verified_mark}"
        )
    lines.append("")
    return lines


# ──────────────────────────────────────────────
# generate_detail_report 섹션 헬퍼
# ──────────────────────────────────────────────


async def _detail_realized_pnl(session: AsyncSession) -> list[str]:
    """실현 수익률 섹션."""
    trades = (await session.execute(
        select(Trade).order_by(Trade.created_at.desc()).limit(20)
    )).scalars().all()

    buy_trades: dict[str, Trade] = {}
    realized: list[dict] = []

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

    if not realized:
        return ["💰 *실현 수익률*: 매매 기록 없음\n"]

    wins = sum(1 for r in realized if r["pnl_pct"] > 0)
    losses = sum(1 for r in realized if r["pnl_pct"] <= 0)
    avg_pnl = sum(r["pnl_pct"] for r in realized) / len(realized)

    lines = [f"💰 *실현 수익률* (승 {wins} / 패 {losses})"]
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
    return lines


def _build_signal_card(idx: int, total: int, row) -> str:
    """시그널 카드 한 장 생성."""
    (
        kst, coin, pat, pat_conf, decision, conf,
        sig_price, fut_price, rsi_raw, macd_h,
        sr_sig, sup, res, bb_upper, bb_lower,
        trend_dir, trend_tf, vol_ratio,
        llm_source, llm_reason,
    ) = row

    d_emoji = {"BUY": "🟢", "SELL": "🔴"}.get(decision, "⏸")
    src = _SRC_LABELS.get(llm_source, llm_source or "?")

    # 4h 후 결과
    if fut_price and sig_price and float(sig_price) > 0:
        chg = (float(fut_price) - float(sig_price)) / float(sig_price) * 100
        if decision == "HOLD":
            mark = "✅" if abs(chg) < 2.0 else "❌"
        elif decision == "BUY":
            mark = "✅" if chg > 0 else "❌"
        else:
            mark = "✅" if chg < 0 else "❌"
        result_str = f"{chg:+.1f}% {mark}"
    else:
        result_str = "⏳ 대기"

    # 지표
    indicators_parts = []
    if rsi_raw:
        indicators_parts.append(f"RSI `{float(rsi_raw):.0f}`")
    if macd_h:
        indicators_parts.append(f"MACD `{float(macd_h):+.0f}`")
    if vol_ratio:
        vr = float(vol_ratio)
        vr_str = f"거래량 `{vr:.1f}x`"
        indicators_parts.append(f"{vr_str} 🔥" if vr >= 1.5 else vr_str)
    ind_line = " | ".join(indicators_parts)

    # S/R
    sr_parts = []
    if sr_sig and sr_sig != "NONE":
        sr_parts.append(_SR_KO.get(sr_sig, sr_sig))
    if sup:
        sr_parts.append(f"지지 `{float(sup):,.0f}`")
    if res:
        sr_parts.append(f"저항 `{float(res):,.0f}`")
    sr_line = " | ".join(sr_parts)

    # BB 위치
    bb_line = ""
    if bb_upper and bb_lower and sig_price:
        sp_f = float(sig_price)
        if sp_f >= float(bb_upper):
            bb_line = "BB 상단 이탈"
        elif sp_f <= float(bb_lower):
            bb_line = "BB 하단 이탈"

    # 패턴
    pat_line = ""
    if pat and pat != "-":
        pat_line = f"패턴: {pat}"
        if pat_conf:
            pat_line += f" ({pat_conf}%)"

    # 추세
    trend_line = ""
    if trend_dir:
        t_emoji = {"UP": "📈", "DOWN": "📉"}.get(trend_dir, "➡️")
        trend_line = f"추세: {t_emoji} {trend_dir} ({trend_tf or ''})"

    # LLM 이유
    reason_line = ""
    if llm_reason:
        r = llm_reason[:80]
        if len(llm_reason) > 80:
            r += "…"
        reason_line = f"💬 {r}"

    # 카드 조립
    card_parts = [
        f"━━━ {idx}/{total} ━━━",
        f"{d_emoji} *{decision} {coin}* ({conf:.0%}) [{src}]",
        f"{kst} | {ind_line}" if ind_line else kst,
    ]
    if sr_line:
        card_parts.append(f"S/R: {sr_line}")
    if pat_line:
        card_parts.append(f"📐 {pat_line}")
    if trend_line:
        card_parts.append(trend_line)
    if bb_line:
        card_parts.append(f"📊 {bb_line}")
    if reason_line:
        card_parts.append(reason_line)
    card_parts.append(f"→ 4h후: {result_str}")

    return "\n".join(card_parts)


async def _detail_signal_cards(session: AsyncSession) -> list[str]:
    """시그널 카드 메시지 목록 (4096자 분할)."""
    recent_llm = (await session.execute(text("""
        SELECT
            to_char(s.created_at AT TIME ZONE 'Asia/Seoul', 'MM/DD HH24:MI') as kst,
            REPLACE(s.market, 'KRW-', '') as coin,
            COALESCE(s.indicators->'patterns'->'patterns'->0->>'name_ko', '-') as pat,
            COALESCE(
                s.indicators->'patterns'->'patterns'->0->>'confidence', ''
            ) as pat_conf,
            s.signal_type as decision,
            s.confidence as conf,
            sp.signal_price,
            fp.future_price,
            COALESCE(
                s.indicators->>'rsi_14',
                s.indicators->'layer_15m'->'indicators'->>'rsi_14'
            ) as rsi,
            COALESCE(
                s.indicators->>'macd_histogram',
                s.indicators->'layer_15m'->'indicators'->>'macd_histogram'
            ) as macd_h,
            s.indicators->'support_resistance'->>'signal' as sr_sig,
            s.indicators->'support_resistance'->>'nearest_support' as sup,
            s.indicators->'support_resistance'->>'nearest_resistance' as res,
            COALESCE(
                s.indicators->>'bb_upper',
                s.indicators->'layer_15m'->'indicators'->>'bb_upper'
            ) as bb_upper,
            COALESCE(
                s.indicators->>'bb_lower',
                s.indicators->'layer_15m'->'indicators'->>'bb_lower'
            ) as bb_lower,
            s.indicators->'higher_trend'->>'direction' as trend_dir,
            s.indicators->'higher_trend'->>'timeframe' as trend_tf,
            COALESCE(
                s.indicators->>'volume_ratio',
                s.indicators->'layer_15m'->'indicators'->>'volume_ratio'
            ) as vol_ratio,
            s.source as llm_source,
            COALESCE(
                s.indicators->>'llm_reason',
                s.indicators->>'tech_reasons',
                ''
            ) as llm_reason
        FROM signals s
        LEFT JOIN LATERAL (
            SELECT c.close as signal_price FROM candles c
            WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
              AND c.open_at <= s.created_at
            ORDER BY c.open_at DESC LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT c.close as future_price FROM candles c
            WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
            AND c.open_at >= s.created_at + interval '4 hours'
            ORDER BY c.open_at ASC LIMIT 1
        ) fp ON true
        WHERE s.source IN ('technical', 'deepseek', 'local_llm')
          AND s.signal_type IN ('BUY', 'SELL')
        ORDER BY s.created_at DESC
        LIMIT 20
    """))).fetchall()

    if not recent_llm:
        return []

    cards = [
        _build_signal_card(idx, len(recent_llm), row)
        for idx, row in enumerate(reversed(recent_llm), 1)
    ]

    # 4096자 이내로 분할
    messages: list[str] = []
    card_header = f"📋 *최근 시그널 이력* ({len(cards)}건)\n"
    card_footer = "\n_결과: ✅적중 ❌실패 ⏳대기 (HOLD: 4h 내 2% 이내=✅)_"

    current_msg = card_header
    for card in cards:
        candidate = current_msg + "\n" + card
        if len(candidate) + len(card_footer) + 10 > 4096:
            messages.append(current_msg.rstrip())
            current_msg = card
        else:
            current_msg = candidate
    current_msg += card_footer
    messages.append(current_msg.rstrip())

    return messages


async def _detail_pattern_accuracy(session: AsyncSession) -> list[str]:
    """패턴별 적중률 (4h 기준) 섹션."""
    pattern_accuracy = (await session.execute(text("""
        WITH pat_eval AS (
            SELECT
                COALESCE(s.indicators->'patterns'->'patterns'
                    ->0->>'name_ko', '패턴없음') as pattern,
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
                WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
                    AND c.open_at <= s.created_at
                ORDER BY c.open_at DESC LIMIT 1
            ) sp ON true
            LEFT JOIN LATERAL (
                SELECT c.close as future_price FROM candles c
                WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
                AND c.open_at >= s.created_at + interval '4 hours'
                ORDER BY c.open_at ASC LIMIT 1
            ) fp ON true
            WHERE s.source IN ('technical', 'deepseek', 'local_llm')
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

    if not pattern_accuracy:
        return []

    lines = ["📐 *패턴별 적중률* (참고: 4h 기준)"]
    lines.append("```")
    lines.append(f"{'패턴':<6} {'판단':<4} {'적중':>4} {'전체':>4} {'적중률':>5}")
    lines.append("-" * 28)
    for row in pattern_accuracy:
        pat, sig, evaluated, hit = row
        rate = f"{hit / evaluated * 100:.0f}%" if evaluated > 0 else "N/A"
        lines.append(f"{pat[:6]:<6} {sig:<4} {hit:>4} {evaluated:>4} {rate:>5}")
    lines.append("```\n")
    return lines


async def _detail_hold_stats(session: AsyncSession) -> list[str]:
    """HOLD 판단 요약 섹션."""
    hold_stats = (await session.execute(text("""
        SELECT
            count(*) as total,
            count(*) FILTER (WHERE abs((fp.future_price - sp.signal_price)
                / sp.signal_price * 100) < 2.0) as stable
        FROM signals s
        LEFT JOIN LATERAL (
            SELECT c.close as signal_price FROM candles c
            WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
              AND c.open_at <= s.created_at
            ORDER BY c.open_at DESC LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT c.close as future_price FROM candles c
            WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
            AND c.open_at >= s.created_at + interval '4 hours'
            ORDER BY c.open_at ASC LIMIT 1
        ) fp ON true
        WHERE s.signal_type = 'HOLD'
          AND fp.future_price IS NOT NULL
          AND sp.signal_price > 0
    """))).fetchone()

    if not hold_stats or hold_stats[0] == 0:
        return []

    total, stable = hold_stats
    return [
        f"⏸️ *HOLD 판단*: {total}건 중 {stable}건 안정"
        f" ({stable / total * 100:.0f}% 가 4h 내 2% 이내)"
    ]


async def _detail_signal_counts(session: AsyncSession) -> list[str]:
    """시그널 현황 섹션."""
    total_counts = (await session.execute(text("""
        SELECT
            count(*) FILTER (
                WHERE source = 'technical'
                AND signal_type IN ('BUY', 'SELL')
            ) as tech_bs,
            count(*) FILTER (
                WHERE source = 'technical' AND signal_type = 'HOLD'
            ) as tech_hold,
            count(*) FILTER (
                WHERE source IN ('deepseek', 'local_llm')
            ) as llm,
            count(*) FILTER (
                WHERE source IN ('deepseek', 'local_llm')
                AND signal_type IN ('BUY', 'SELL')
            ) as llm_bs,
            count(*) FILTER (
                WHERE source IN ('deepseek', 'local_llm')
                AND signal_type = 'HOLD'
            ) as llm_hold
        FROM signals
    """))).fetchone()

    if not total_counts:
        return []

    tech_bs, tech_hold, llm, llm_bs, llm_hold = total_counts
    return [
        f"\n📊 시그널 `{tech_bs}건` (HOLD `{tech_hold}`)"
        f" | 충격감지 LLM `{llm}건`"
        f" (BUY/SELL `{llm_bs}` HOLD `{llm_hold}`)"
    ]


# ──────────────────────────────────────────────
# 메인 함수
# ──────────────────────────────────────────────


async def generate_report(
    session: AsyncSession,
    accounts: list[dict] | None = None,
    ticker_prices: dict[str, float] | None = None,
) -> str:
    """수익률 + 적중률 리포트 텍스트 생성."""
    now = datetime.now(UTC)
    kst_now = now + timedelta(hours=9)
    lines = [f"📊 *수익률 리포트* ({kst_now:%Y-%m-%d %H:%M} KST)\n"]

    lines.extend(_section_holdings(accounts, ticker_prices))
    lines.extend(await _section_daily_trades(session, now))
    lines.extend(await _section_topdown_indicators(session))
    lines.extend(await _section_recent_signals(session, now))

    return "\n".join(lines)


async def generate_detail_report(session: AsyncSession) -> list[str]:
    """시그널 상세 리포트 — 메시지 분할 반환.

    Returns:
        list[str]: 텔레그램 메시지 목록 (각 4096자 이내)
    """
    now = datetime.now(UTC)
    kst_now = now + timedelta(hours=9)
    lines = [f"🔍 *상세 리포트* ({kst_now:%m/%d %H:%M} KST)\n"]

    lines.extend(await _detail_realized_pnl(session))

    card_messages = await _detail_signal_cards(session)
    lines.append(
        f"📋 *시그널 이력*: {len(card_messages)}건"
        + (" (카드 상세는 다음 메시지)" if card_messages else " (기록 없음)")
    )
    lines.append("")

    lines.extend(await _detail_pattern_accuracy(session))
    lines.extend(await _detail_hold_stats(session))
    lines.extend(await _detail_signal_counts(session))

    summary = "\n".join(lines)
    return [summary] + card_messages


# ──────────────────────────────────────────────
# 시그널 요약 (N시간)
# ──────────────────────────────────────────────


def _fmt_macd(val: float) -> str:
    """MACD 값을 K 접미사 포맷으로 변환."""
    if abs(val) >= 1000:
        return f"{val / 1000:+.0f}K"
    return f"{val:+.0f}"


def _fmt_change(sig_price: float, fut_price: float, signal_type: str) -> tuple[str, str]:
    """4h후 변동률 + 결과 판정."""
    chg = (fut_price - sig_price) / sig_price * 100
    chg_str = f"{chg:+.1f}%"
    if signal_type == "BUY":
        mark = "✅" if chg > 0 else "❌"
    else:
        mark = "✅" if chg < 0 else "❌"
    return chg_str, mark


async def _query_buy_sell(session: AsyncSession, hours: int) -> list:
    """BUY/SELL 시그널 + 4h후 가격 조회."""
    return (await session.execute(text(f"""
        SELECT
            to_char(s.created_at AT TIME ZONE 'Asia/Seoul', 'HH24:MI') as kst,
            REPLACE(s.market, 'KRW-', '') as coin,
            s.signal_type,
            s.confidence,
            ROUND(COALESCE(
                (s.indicators->>'rsi_14')::numeric,
                (s.indicators->'layer_15m'->'indicators'->>'rsi_14')::numeric
            ), 0) as rsi,
            ROUND(COALESCE(
                (s.indicators->>'macd_histogram')::numeric,
                (s.indicators->'layer_15m'->'indicators'->>'macd_histogram')::numeric
            ), 0) as macd_h,
            sp.signal_price,
            fp.future_price
        FROM signals s
        LEFT JOIN LATERAL (
            SELECT c.close as signal_price FROM candles c
            WHERE c.market = s.market AND c.timeframe = '15m'
              AND c.open_at <= s.created_at
            ORDER BY c.open_at DESC LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT c.close as future_price FROM candles c
            WHERE c.market = s.market AND c.timeframe = '15m'
              AND c.open_at >= s.created_at + interval '4 hours'
            ORDER BY c.open_at ASC LIMIT 1
        ) fp ON true
        WHERE s.source IN ('technical', 'deepseek', 'local_llm')
          AND s.signal_type IN ('BUY', 'SELL')
          AND s.created_at >= now() - interval '{hours} hours'
          AND fp.future_price IS NOT NULL
        ORDER BY s.created_at
    """))).fetchall()


async def _query_hold_stats(session: AsyncSession, hours: int) -> tuple[int, int]:
    """HOLD 통계 조회."""
    row = (await session.execute(text(f"""
        SELECT
            count(*) as total,
            count(*) FILTER (
                WHERE abs((fp.fp - sp.sp) / sp.sp * 100) < 2.0
            ) as stable
        FROM signals s
        LEFT JOIN LATERAL (
            SELECT c.close as sp FROM candles c
            WHERE c.market = s.market AND c.timeframe = '15m'
              AND c.open_at <= s.created_at
            ORDER BY c.open_at DESC LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT c.close as fp FROM candles c
            WHERE c.market = s.market AND c.timeframe = '15m'
              AND c.open_at >= s.created_at + interval '4 hours'
            ORDER BY c.open_at ASC LIMIT 1
        ) fp ON true
        WHERE s.signal_type = 'HOLD'
          AND s.created_at >= now() - interval '{hours} hours'
          AND fp.fp IS NOT NULL AND sp.sp > 0
    """))).fetchone()
    if not row or row[0] == 0:
        return 0, 0
    return int(row[0]), int(row[1])


def _build_summary_table(rows: list) -> tuple[str, int, int]:
    """BUY/SELL 테이블 텍스트 + 적중/확정 카운트."""
    header = "시간  코인 판단 신뢰  RSI MACD   4h후  결과"
    lines = [header]
    hit, confirmed = 0, 0

    for row in rows:
        kst, coin, sig_type, conf, rsi, macd_h, sig_price, fut_price = row
        conf_str = f"{int(float(conf) * 100)}%" if conf else "N/A"
        rsi_str = f"{int(rsi)}" if rsi is not None else " -"
        macd_str = _fmt_macd(float(macd_h)) if macd_h is not None else "  -"

        if fut_price and sig_price and float(sig_price) > 0:
            chg_str, mark = _fmt_change(float(sig_price), float(fut_price), sig_type)
            confirmed += 1
            if mark == "✅":
                hit += 1
        else:
            chg_str, mark = "  -  ", "⏳"

        side = "BUY " if sig_type == "BUY" else "SELL"
        lines.append(
            f"{kst} {coin:<4} {side} {conf_str:>4}"
            f"  {rsi_str:>3} {macd_str:>5}"
            f"  {chg_str:>6}  {mark}"
        )

    return "\n".join(lines), hit, confirmed


async def generate_signal_summary(session: AsyncSession, *, hours: int = 12) -> str:
    """N시간 시그널 결과 요약 텍스트 생성."""
    rows = await _query_buy_sell(session, hours)
    hold_total, hold_stable = await _query_hold_stats(session, hours)

    parts: list[str] = [f"📡 *{hours}시간 시그널 결과*\n"]

    if rows:
        table_text, hit, confirmed = _build_summary_table(rows)
        parts.append(f"```\n{table_text}\n```")
        if confirmed > 0:
            rate = hit / confirmed * 100
            parts.append(f"적중: {hit}/{confirmed} ({rate:.0f}%)")
    else:
        parts.append("BUY/SELL 시그널 없음")

    if hold_total > 0:
        pct = hold_stable / hold_total * 100
        parts.append(f"⏸ HOLD {hold_total}건 — {hold_stable}건({pct:.0f}%) 2% 이내")

    return "\n".join(parts)

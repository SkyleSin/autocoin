"""수익률 분석 리포트 생성."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

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

    # ── 4. 패턴 적중률 (누적, pattern_configs 기반) ──
    accuracy = await get_accuracy_summary(session, include_inactive=True)
    if accuracy:
        # DB에서 영한 매핑 로드 (폴백: 하드코딩)
        try:
            from services.pattern_service import get_pattern_ko
            pattern_ko = await get_pattern_ko(session)
        except Exception:
            pattern_ko = _PATTERN_KO
        lines.append("🎯 *패턴 적중률 (누적)*")
        total_all = sum(a["total"] for a in accuracy if a["total"] > 0)
        hit_all = sum(a["hit"] for a in accuracy if a["total"] > 0)
        for a in accuracy:
            ko = pattern_ko.get(
                a["pattern"], _PATTERN_KO.get(a["pattern"], a["pattern"])
            )
            if a["total"] > 0:
                tag = "" if a.get("active", True) else " 📋관찰"
                lines.append(
                    f"• {ko}: `{a['accuracy']:.0f}%`"
                    f" ({a['hit']}/{a['total']}){tag}"
                )
            elif not a.get("active", True):
                lines.append(f"• {ko}: `N/A` (0건) 📋관찰")
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

    # ── 6. 섀도우 트레이딩 (자동화 가정) ──
    try:
        from services.shadow_service import get_shadow_summary
        shadow = await get_shadow_summary(session)
        if shadow["buy_count"] > 0 or shadow["sell_count"] > 0:
            lines.append("🔮 *섀도우 트레이딩* (자동화 가정)")
            lines.append(f"• 수익률: `{shadow['return_pct']:+.2f}%`")
            lines.append(
                f"• 매수 `{shadow['buy_count']}건` / 매도 `{shadow['sell_count']}건`"
            )
            if shadow["sell_count"] > 0:
                lines.append(f"• 승률: `{shadow['win_rate']:.0f}%`")
            lines.append(
                f"• 실현 손익: `{shadow['total_pnl']:+,.0f}원`"
                f" | 수수료: `{shadow['total_fees']:,.0f}원`"
            )
            if shadow["positions"]:
                for p in shadow["positions"]:
                    lines.append(
                        f"• {p['market']}: `{p['qty']:.4f}` @ `{p['avg_price']:,.0f}`"
                    )
            lines.append("")
    except Exception:
        pass  # 섀도우 테이블 미생성 시 무시

    return "\n".join(lines)


async def generate_detail_report(session: AsyncSession) -> list[str]:
    """LLM 판단 상세 리포트 — 메시지 분할 반환.

    Returns:
        list[str]: 텔레그램 메시지 목록 (각 4096자 이내)
    """
    now = datetime.now(UTC)
    kst_now = now + timedelta(hours=9)
    messages: list[str] = []

    # ── 메시지 1: 요약 (실현 수익률 + 적중률 + 통계) ──
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

    # ── 2. 최근 LLM 판단 이력 (카드형) ──
    _SR_KO = {
        "SUPPORT_BOUNCE": "지지 반등",
        "SUPPORT_BREAK": "지지 이탈",
        "RESISTANCE_BREAK": "저항 돌파",
        "RESISTANCE_REJECT": "저항 저항",
    }

    recent_llm = (await session.execute(text("""
        SELECT
            to_char(s.created_at AT TIME ZONE 'Asia/Seoul', 'MM/DD HH24:MI') as kst,
            REPLACE(s.market, 'KRW-', '') as coin,
            COALESCE(s.indicators->'patterns'->'patterns'->0->>'name_ko', '-') as pattern,
            COALESCE(
                s.indicators->'patterns'->'patterns'->0->>'confidence', ''
            ) as pat_conf,
            s.signal_type as decision,
            s.confidence as conf,
            sp.signal_price,
            fp.future_price,
            s.indicators->>'rsi_14' as rsi,
            s.indicators->>'macd_histogram' as macd_h,
            s.indicators->'support_resistance'->>'signal' as sr_sig,
            s.indicators->'support_resistance'->>'nearest_support' as sup,
            s.indicators->'support_resistance'->>'nearest_resistance' as res,
            s.indicators->>'bb_upper' as bb_upper,
            s.indicators->>'bb_lower' as bb_lower,
            s.indicators->'higher_trend'->>'direction' as trend_dir,
            s.indicators->'higher_trend'->>'timeframe' as trend_tf,
            s.indicators->>'volume_ratio' as vol_ratio,
            s.source as llm_source,
            COALESCE(s.indicators->>'llm_reason', '') as llm_reason
        FROM signals s
        LEFT JOIN LATERAL (
            SELECT c.close as signal_price FROM candles c
            WHERE c.market = s.market AND c.timeframe IN ('5m','15m') AND c.open_at <= s.created_at
            ORDER BY c.open_at DESC LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT c.close as future_price FROM candles c
            WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
            AND c.open_at >= s.created_at + interval '4 hours'
            ORDER BY c.open_at ASC LIMIT 1
        ) fp ON true
        WHERE s.source IN ('deepseek', 'local_llm')
        ORDER BY s.created_at DESC
        LIMIT 20
    """))).fetchall()

    # 카드 생성
    cards: list[str] = []
    if recent_llm:
        for idx, row in enumerate(reversed(recent_llm), 1):
            (
                kst, coin, pattern, pat_conf, decision, conf,
                sig_price, fut_price, rsi_raw, macd_h,
                sr_sig, sup, res, bb_upper, bb_lower,
                trend_dir, trend_tf, vol_ratio,
                llm_source, llm_reason,
            ) = row

            # 판단 이모지
            if decision == "BUY":
                d_emoji = "🟢"
            elif decision == "SELL":
                d_emoji = "🔴"
            else:
                d_emoji = "⏸"

            # LLM 소스 라벨
            src = "DeepSeek R1" if llm_source == "deepseek" else "Ollama"

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

            # 지표 라인
            indicators_parts = []
            if rsi_raw:
                indicators_parts.append(f"RSI `{float(rsi_raw):.0f}`")
            if macd_h:
                indicators_parts.append(f"MACD `{float(macd_h):+.0f}`")
            if vol_ratio:
                vr = float(vol_ratio)
                if vr >= 1.5:
                    indicators_parts.append(f"거래량 `{vr:.1f}x` 🔥")
                else:
                    indicators_parts.append(f"거래량 `{vr:.1f}x`")
            ind_line = " | ".join(indicators_parts) if indicators_parts else ""

            # S/R 라인
            sr_parts = []
            if sr_sig and sr_sig != "NONE":
                sr_parts.append(_SR_KO.get(sr_sig, sr_sig))
            if sup:
                sr_parts.append(f"지지 `{float(sup):,.0f}`")
            if res:
                sr_parts.append(f"저항 `{float(res):,.0f}`")
            sr_line = " | ".join(sr_parts) if sr_parts else ""

            # BB 위치
            bb_line = ""
            if bb_upper and bb_lower and sig_price:
                sp_f = float(sig_price)
                bbu = float(bb_upper)
                bbl = float(bb_lower)
                if sp_f >= bbu:
                    bb_line = "BB 상단 이탈"
                elif sp_f <= bbl:
                    bb_line = "BB 하단 이탈"

            # 패턴 라인
            pat_line = ""
            if pattern and pattern != "-":
                pat_line = f"패턴: {pattern}"
                if pat_conf:
                    pat_line += f" ({pat_conf}%)"

            # 추세 라인
            trend_line = ""
            if trend_dir:
                t_emoji = {"UP": "📈", "DOWN": "📉"}.get(trend_dir, "➡️")
                tf_label = trend_tf or ""
                trend_line = f"추세: {t_emoji} {trend_dir} ({tf_label})"

            # LLM 이유 (80자 제한)
            reason_line = ""
            if llm_reason:
                r = llm_reason[:80]
                if len(llm_reason) > 80:
                    r += "…"
                reason_line = f"💬 {r}"

            # 카드 조립
            card_parts = [
                f"━━━ {idx}/{len(recent_llm)} ━━━",
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

            cards.append("\n".join(card_parts))

    # 카드들을 4096자 이내로 분할하여 메시지 생성
    if cards:
        card_header = f"📋 *최근 LLM 판단* ({len(cards)}건)\n"
        card_footer = "\n_결과: ✅적중 ❌실패 ⏳대기 (HOLD: 4h 내 2% 이내=✅)_"

        current_msg = card_header
        for card in cards:
            candidate = current_msg + "\n" + card
            # 4096 - footer 여유분
            if len(candidate) + len(card_footer) + 10 > 4096:
                messages.append(current_msg.rstrip())
                current_msg = card
            else:
                current_msg = candidate
        current_msg += card_footer
        messages.append(current_msg.rstrip())

    # 요약 메시지에 LLM 판단 카드 대신 간략 통계만
    lines.append(
        f"📋 *LLM 판단*: {len(cards)}건"
        + (" (카드 상세는 다음 메시지)" if cards else " (기록 없음)")
    )
    lines.append("")

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
                WHERE c.market = s.market AND c.timeframe IN ('5m','15m') AND c.open_at <= s.created_at
                ORDER BY c.open_at DESC LIMIT 1
            ) sp ON true
            LEFT JOIN LATERAL (
                SELECT c.close as future_price FROM candles c
                WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
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

    # ── 4-1. 전체 패턴 적중률 (활성 + 비활성) ──
    all_accuracy = await get_accuracy_summary(session, include_inactive=True)
    if all_accuracy:
        try:
            from services.pattern_service import get_pattern_ko
            pattern_ko = await get_pattern_ko(session)
        except Exception:
            pattern_ko = _PATTERN_KO
        active_acc = [a for a in all_accuracy if a.get("active", True)]
        inactive_acc = [a for a in all_accuracy if not a.get("active", True)]
        lines.append("📐 *전체 패턴 적중률*")
        lines.append("```")
        lines.append(f"{'패턴':<6} {'감지':>4} {'적중':>4} {'적중률':>5} {'상태':>4}")
        lines.append("-" * 30)
        for row in active_acc:
            ko = pattern_ko.get(
                row["pattern"], _PATTERN_KO.get(row["pattern"], row["pattern"])
            )
            rate = f"{row['accuracy']:.0f}%" if row["total"] > 0 else "N/A"
            lines.append(
                f"{ko[:6]:<6} {row['total']:>4}"
                f" {row['hit']:>4} {rate:>5} {'활성':>4}"
            )
        for row in inactive_acc:
            ko = pattern_ko.get(
                row["pattern"], _PATTERN_KO.get(row["pattern"], row["pattern"])
            )
            rate = f"{row['accuracy']:.0f}%" if row["total"] > 0 else "N/A"
            lines.append(
                f"{ko[:6]:<6} {row['total']:>4}"
                f" {row['hit']:>4} {rate:>5} {'관찰':>4}"
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
            WHERE c.market = s.market AND c.timeframe IN ('5m','15m') AND c.open_at <= s.created_at
            ORDER BY c.open_at DESC LIMIT 1
        ) sp ON true
        LEFT JOIN LATERAL (
            SELECT c.close as future_price FROM candles c
            WHERE c.market = s.market AND c.timeframe IN ('5m','15m')
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

    # ── 7. 섀도우 트레이딩 상세 ──
    try:
        from services.shadow_service import get_shadow_summary
        shadow = await get_shadow_summary(session)
        if shadow["buy_count"] > 0 or shadow["sell_count"] > 0:
            lines.append("\n🔮 *섀도우 트레이딩* (자동화 가정)")
            lines.append("```")
            lines.append(f"초기 자본:  1,000,000원")
            lines.append(f"현재 평가:  {shadow['total_value']:>12,.0f}원")
            lines.append(f"수익률:     {shadow['return_pct']:>+11.2f}%")
            lines.append(f"실현 손익:  {shadow['total_pnl']:>+12,.0f}원")
            lines.append(f"수수료:     {shadow['total_fees']:>12,.0f}원")
            lines.append(f"매수/매도:  {shadow['buy_count']}건 / {shadow['sell_count']}건")
            if shadow["sell_count"] > 0:
                lines.append(f"승률:       {shadow['win_rate']:>11.0f}%")
            lines.append("```")
            if shadow["positions"]:
                lines.append("_보유:_")
                for p in shadow["positions"]:
                    lines.append(
                        f"  {p['market']}: `{p['qty']:.4f}` @ `{p['avg_price']:,.0f}`"
                    )
            lines.append("")
    except Exception:
        pass

    # 요약 메시지를 맨 앞에, 카드 메시지들을 뒤에 배치
    summary = "\n".join(lines)
    return [summary] + messages



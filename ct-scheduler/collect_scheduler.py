"""수집 스케줄러 — 1분 루프: 봉 수집 트리거 + 지표 분석 + 리포트."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

import pandas as pd
from analysis.indicators import compute_all
from analysis.patterns import analyze as analyze_patterns
from analysis.signal_generator import evaluate as evaluate_signal
from analysis.support_resistance import analyze as analyze_sr
from infra.api.candle import get_candles
from infra.api.indicator_snapshot import upsert_indicator_snapshot
from infra.api.job import cleanup_jobs
from infra.api.portfolio import get_user_portfolio
from infra.api.report import generate_report, get_signal_summary
from infra.api.signal import cleanup_hold_signals
from infra.api.tracked_asset import get_active_markets
from infra.api.trade import get_trades
from infra.api.user import get_active_users
from infra.redis_pubsub import publish_analysis_ready, publish_master, publish_telegram
from infra.redis_queue import push_trade_job
from utils.config import Settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
REPORT_HOURS_KST = [9, 21]

# 분석 대상 타임프레임 → 분 단위 주기
TF_PERIODS: dict[str, int] = {
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

def _tf_bucket(now: datetime, period_min: int) -> int:
    """현재 시각이 속한 TF 버킷 인덱스 — 버킷이 바뀌면 경계 통과."""
    epoch_min = int(now.timestamp()) // 60
    return epoch_min // period_min


def _next_kst_target(hour: int, minute: int) -> datetime:
    now_kst = datetime.now(KST)
    target = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_kst >= target:
        target += timedelta(days=1)
    return target.astimezone(UTC)


async def _build_user_report(
    user_id: int, price_map: dict[str, float],
) -> str | None:
    """유저 손익 리포트 텍스트 생성 (cmd_report 로직 재현)."""
    portfolio = await get_user_portfolio(user_id)
    if not portfolio:
        return None

    total_invested = 0.0
    total_current = 0.0
    coin_lines = []

    for item in portfolio:
        currency = item["currency"]
        if currency == "KRW":
            continue
        balance = float(item["balance"])
        avg_price = float(item.get("avg_buy_price", 0))
        if balance <= 0 or avg_price <= 0:
            continue
        current_price = price_map.get(currency)
        if not current_price:
            continue

        invested = avg_price * balance
        current_val = current_price * balance
        pnl = current_val - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0
        total_invested += invested
        total_current += current_val

        emoji = "🟢" if pnl >= 0 else "🔴"
        coin_lines.append(
            f"{emoji} `{currency}`: {pnl:+,.0f}원 ({pnl_pct:+.1f}%)"
            f" | 평균가 {avg_price:,.0f}"
        )

    total_pnl = total_current - total_invested
    total_pnl_pct = (
        (total_pnl / total_invested * 100) if total_invested > 0 else 0
    )

    lines = ["📊 *손익 리포트*\n"]
    lines.append(f"💰 총 평가액: {total_current:,.0f}원")
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    lines.append(
        f"{pnl_emoji} 총 손익: {total_pnl:+,.0f}원 ({total_pnl_pct:+.1f}%)\n"
    )
    if coin_lines:
        lines.append("*코인별 손익:*")
        lines.extend(coin_lines)

    trades = await get_trades(limit=5, user_id=user_id)
    if trades:
        lines.append("\n*최근 거래:*")
        for t in trades:
            side_emoji = "🟢" if t.get("side") == "buy" else "🔴"
            created = t.get("created_at", "")[:16].replace("T", " ")
            lines.append(
                f"{side_emoji} `{t['market']}` {t['side']}"
                f" p={t['price_krw']} [{t['status']}] {created}"
            )

    return "\n".join(lines)


def _next_report_target() -> datetime:
    now_kst = datetime.now(KST)
    today = now_kst.replace(minute=0, second=0, microsecond=0)
    candidates = [today.replace(hour=h) for h in REPORT_HOURS_KST]
    tomorrow = today + timedelta(days=1)
    candidates += [tomorrow.replace(hour=h) for h in REPORT_HOURS_KST]
    future = [c for c in candidates if c > now_kst]
    return min(future).astimezone(UTC)


# ---------------------------------------------------------------------------
# 봉 수집 트리거
# ---------------------------------------------------------------------------

async def _trigger_collect(markets: list[str], timeframe: str) -> None:
    """ct-worker에 캔들 수집 Job 전달."""
    for market in markets:
        await push_trade_job({
            "type": "collect_candles",
            "payload": {"market": market, "timeframe": timeframe},
        })
    logger.info("candle trigger: %s × %d markets → Redis", timeframe, len(markets))


async def _trigger_portfolio_sync() -> None:
    """활성 유저별 포트폴리오 동기화 Job → ct-worker."""
    try:
        users = await get_active_users()
    except Exception:
        logger.exception("portfolio sync: 유저 목록 조회 실패")
        return

    for user in users:
        user_id = user.get("id")
        if user_id is None:
            continue
        await push_trade_job({
            "type": "sync_portfolio",
            "user_id": str(user_id),
            "payload": {"user_id": user_id},
        })
    logger.info("portfolio sync trigger: %d users → Redis", len(users))


# ---------------------------------------------------------------------------
# 지표 분석
# ---------------------------------------------------------------------------

async def _analyze_market(
    market: str, timeframe: str, last_open_at: str | None,
) -> str | None:
    """ct-api에서 봉 조회 → 지표/SR/패턴 계산 → 스냅샷 저장.

    Returns:
        분석된 최신 봉의 open_at 값 (새 봉이 없으면 None).
    """
    candles = await get_candles(market, timeframe, limit=200, order="desc")
    if not candles:
        return None

    latest_open_at = candles[0].get("open_at") or candles[0].get("opened_at")
    if latest_open_at == last_open_at:
        return None  # 새 봉 없음 → 분석 스킵

    # desc → asc 정렬
    candles_asc = list(reversed(candles))

    closes = pd.Series([float(c["close"]) for c in candles_asc])
    highs = pd.Series([float(c["high"]) for c in candles_asc])
    lows = pd.Series([float(c["low"]) for c in candles_asc])
    volumes = pd.Series([float(c.get("volume", 0)) for c in candles_asc])
    close_price = float(closes.iloc[-1])

    # 지표 계산
    snap = compute_all(closes, volumes)
    if snap is None:
        logger.debug("지표 계산 불가 (데이터 부족): %s %s", market, timeframe)
        return latest_open_at

    # S/R 분석
    try:
        sr = analyze_sr(highs, lows, closes)
    except Exception:
        logger.exception("SR 분석 실패: %s %s", market, timeframe)
        sr = None

    # 시그널 평가
    try:
        signal_result = evaluate_signal(
            snap, close_price, timeframe=timeframe, sr_analysis=sr,
        )
    except Exception:
        logger.exception("시그널 평가 실패: %s %s", market, timeframe)
        signal_result = None

    # 패턴 분석
    try:
        pattern_result = analyze_patterns(
            highs, lows, closes, timeframe=timeframe, volumes=volumes,
        )
    except Exception:
        logger.exception("패턴 분석 실패: %s %s", market, timeframe)
        pattern_result = None

    # 스냅샷 저장 — indicators JSONB에 모든 분석 결과 포함
    indicators_dict: dict = {
        **snap.to_dict(),
        "close_price": close_price,
        "support_resistance": sr.to_dict() if sr else {},
        "patterns": pattern_result.to_dict() if pattern_result else {},
        "signal": signal_result.signal_type if signal_result else "HOLD",
        "confidence": signal_result.confidence if signal_result else 0.0,
        "reasons": signal_result.reasons if signal_result else [],
    }
    payload: dict = {
        "market": market,
        "timeframe": timeframe,
        "candle_open_at": latest_open_at,
        "indicators": indicators_dict,
    }
    try:
        await upsert_indicator_snapshot(payload)
        logger.debug("snapshot 저장: %s %s signal=%s", market, timeframe,
                     indicators_dict["signal"])
    except Exception:
        logger.exception("snapshot 저장 실패: %s %s", market, timeframe)
        return latest_open_at

    # ct-strategy에 전략 평가 요청
    try:
        await publish_analysis_ready(market, timeframe)
    except Exception:
        logger.exception("analysis_ready 발행 실패: %s %s", market, timeframe)

    return latest_open_at


# ---------------------------------------------------------------------------
# 정기 리포트 발송
# ---------------------------------------------------------------------------


async def _send_scheduled_reports(settings: Settings) -> None:
    """9시/21시 정기 리포트 — master + 유저별 발송."""
    # 12h 시그널 요약 (공통)
    signal_text = ""
    try:
        resp = await get_signal_summary(hours=16)
        signal_text = resp.get("text", "")
    except Exception:
        logger.exception("report: 시그널 요약 조회 실패")

    # 1) master 봇 — 기존 generate_report + 시그널 요약
    master_chat = settings.master_telegram_id
    if master_chat:
        try:
            resp = await generate_report()
            master_text = resp.get("text", "")
            if signal_text:
                master_text += f"\n\n{signal_text}"
            await publish_master(master_chat, master_text)
            logger.info("report: master 리포트 발송")
        except Exception:
            logger.exception("report: master 리포트 실패")

    # 2) 유저별 — 손익 리포트 + 시그널 요약
    try:
        users = await get_active_users()
    except Exception:
        logger.exception("report: 유저 조회 실패")
        return

    # 현재가 맵 — 유저별 중복 호출 방지
    try:
        assets = await get_active_markets()
        price_map = {
            a["market"].replace("KRW-", ""): a.get("current_price")
            for a in assets
        }
    except Exception:
        logger.exception("report: 마켓 조회 실패")
        price_map = {}

    for user in users:
        chat_id = str(user.get("telegram_id", ""))
        user_id = user.get("id")
        if not chat_id or not user_id:
            continue
        try:
            user_text = await _build_user_report(user_id, price_map)
            if not user_text:
                user_text = "📊 보유 자산이 없습니다."
            if signal_text:
                user_text += f"\n\n{signal_text}"
            await publish_telegram(chat_id, user_text)
            logger.info("report: 유저 %s 리포트 발송", chat_id)
        except Exception:
            logger.exception("report: 유저 %s 리포트 실패", chat_id)


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------

async def collect_scheduler(settings: Settings) -> None:
    """수집 스케줄러 메인 루프 (1분 주기).

    흐름:
      1. TF 경계 감지 → ct-worker에 collect_candles Job 전달
      2. ct-api에서 봉 조회 → 지표/SR/패턴 분석 → 스냅샷 저장 (중복 방지)
      3. 일별 태스크 (Job 정리) / 리포트 발송
    """
    logger.info("collect_scheduler: 시작")

    # 중복 분석 방지: (market, timeframe) → 마지막 분석 open_at
    last_open_at: dict[tuple[str, str], str | None] = {}
    # TF 경계 추적: timeframe → 마지막 버킷 인덱스
    last_bucket: dict[str, int] = {}

    next_daily = _next_kst_target(0, 5)
    next_report = _next_report_target()

    try:
        while True:
            tick_start = datetime.now(UTC)

            # 활성 마켓 조회
            try:
                assets = await get_active_markets()
                markets = [a["market"] for a in assets] if assets else []
            except Exception:
                logger.exception("collect: 마켓 조회 실패")
                markets = []

            # TF 경계 확인 → 봉 수집 트리거 (1분 지연: 봉 마감 확정 대기)
            delayed = tick_start - timedelta(minutes=1)
            for tf, period_min in TF_PERIODS.items():
                bucket = _tf_bucket(delayed, period_min)
                if last_bucket.get(tf) != bucket:
                    last_bucket[tf] = bucket
                    if markets:
                        try:
                            await _trigger_collect(markets, tf)
                        except Exception:
                            logger.exception("candle trigger 실패: %s", tf)

                    # 15m 경계 → 포트폴리오 동기화 (전략 평가 전 잔고 갱신)
                    if tf == "15m":
                        try:
                            await _trigger_portfolio_sync()
                        except Exception:
                            logger.exception("portfolio sync trigger 실패")

            # 분석: ct-api 봉 조회 → 지표/신호 저장
            for tf in TF_PERIODS:
                for market in markets:
                    key = (market, tf)
                    try:
                        new_open_at = await _analyze_market(
                            market, tf, last_open_at.get(key),
                        )
                        if new_open_at:
                            last_open_at[key] = new_open_at
                    except Exception:
                        logger.exception("분석 실패: %s %s", market, tf)

            # 일별 태스크
            now = datetime.now(UTC)
            if now >= next_daily:
                try:
                    await cleanup_jobs(complete_days=3, error_days=7)
                    logger.info("collect/daily: job cleanup 완료")
                except Exception:
                    logger.exception("collect/daily: job cleanup 실패")
                # HOLD 시그널 7일 정리
                try:
                    await cleanup_hold_signals(days=7)
                    logger.info("collect/daily: HOLD 시그널 정리 완료")
                except Exception:
                    logger.exception("collect/daily: HOLD 시그널 정리 실패")
                # 마켓 목록 동기화
                try:
                    await push_trade_job({"type": "sync_markets", "payload": {}})
                    logger.info("collect/daily: 마켓 동기화 트리거")
                except Exception:
                    logger.exception("collect/daily: 마켓 동기화 트리거 실패")
                next_daily = _next_kst_target(0, 5)

            # 리포트 발송
            if now >= next_report:
                await _send_scheduled_reports(settings)
                next_report = _next_report_target()

            # 다음 틱까지 대기 (10초 주기)
            elapsed = (datetime.now(UTC) - tick_start).total_seconds()
            await asyncio.sleep(max(0.0, 10.0 - elapsed))

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("collect_scheduler: 종료")

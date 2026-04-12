"""통합 스케줄러 — 캔들 수집, 일봉 수집/판정, 리포트 전송을 단일 루프로 관리.

기존 3개 독립 스케줄러(candle_scheduler, daily_candle_scheduler, report_scheduler)를
하나의 asyncio 루프로 통합. 가장 가까운 태스크까지 sleep → 실행 → 다음 시각 재계산.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from utils.config import Settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

REPORT_HOURS_KST = [9, 21]


# ---------------------------------------------------------------------------
# 다음 실행 시각 계산
# ---------------------------------------------------------------------------

def _next_kst_target(hour: int, minute: int) -> datetime:
    """다음 KST 지정 시각을 UTC datetime으로 반환."""
    now_kst = datetime.now(KST)
    target = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_kst >= target:
        target += timedelta(days=1)
    return target.astimezone(UTC)


def _next_report_target() -> datetime:
    """다음 리포트 시각 (KST 09:00 또는 21:00)을 UTC datetime으로 반환."""
    now_kst = datetime.now(KST)
    today = now_kst.replace(minute=0, second=0, microsecond=0)
    candidates = [today.replace(hour=h) for h in REPORT_HOURS_KST]
    tomorrow = today + timedelta(days=1)
    candidates += [tomorrow.replace(hour=h) for h in REPORT_HOURS_KST]
    future = [c for c in candidates if c > now_kst]
    return min(future).astimezone(UTC)


# ---------------------------------------------------------------------------
# 태스크별 실행 함수
# ---------------------------------------------------------------------------

async def _tick_candle(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """캔들 수집 Job 생성 (분석TF + 참조TF)."""
    from collector.scheduler import (
        _LABEL_TO_UNIT,
        _active_markets,
        _create_candle_jobs,
    )

    unit = settings.candle_unit
    count = settings.candle_count

    async with session_factory() as session:
        markets = await _active_markets(session)
        if not markets:
            logger.debug("scheduler/candle: no active markets, skip")
            return

        created = await _create_candle_jobs(session, markets, unit, count)
        logger.info(
            "scheduler/candle: created %d jobs for %d markets",
            created, len(markets),
        )

        ref_tf_str = settings.candle_ref_timeframes
        if ref_tf_str.strip():
            for tf_label in ref_tf_str.split(","):
                tf_label = tf_label.strip()
                ref_unit = _LABEL_TO_UNIT.get(tf_label)
                if ref_unit and ref_unit != unit:
                    ref_created = await _create_candle_jobs(
                        session, markets, ref_unit, count, ref_only=True,
                    )
                    logger.info(
                        "scheduler/candle: created %d ref jobs (%s)",
                        ref_created, tf_label,
                    )


async def _tick_daily(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """일봉 수집 + 패턴 적중률 판정 + Job 정리."""
    from collector.daily_scheduler import (
        _active_markets,
        _cleanup_old_jobs,
        _collect_latest_daily,
        _sync_pattern_cache,
    )
    from services.pattern_service import evaluate_pending_signals

    async with session_factory() as session:
        markets = await _active_markets(session)
        if markets:
            total = await _collect_latest_daily(session, settings, markets)
            logger.info(
                "scheduler/daily: collected %d daily candles for %d markets",
                total, len(markets),
            )

        # 패턴 적중률 판정 (7일 전 시그널)
        try:
            pat_markets = [
                m.strip()
                for m in settings.pattern_markets.split(",")
                if m.strip()
            ]
            eval_result = await evaluate_pending_signals(
                session, pattern_markets=pat_markets,
            )
            if eval_result["evaluated"] > 0:
                logger.info(
                    "scheduler/daily: pattern eval %d evaluated, %d hit",
                    eval_result["evaluated"], eval_result["hit"],
                )
                await _sync_pattern_cache(session)
        except Exception:
            logger.exception("scheduler/daily: pattern evaluation failed")

        await _cleanup_old_jobs(session)


async def _tick_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """수익률 리포트 생성 + 텔레그램 전송."""
    from services.report_scheduler import _fetch_balances
    from services.report_service import generate_report
    from telegram.bot import send_message

    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not (token and chat_id):
        return

    accounts, ticker_prices = await _fetch_balances(settings)
    async with session_factory() as session:
        report_text = await generate_report(
            session, accounts=accounts, ticker_prices=ticker_prices,
        )
        await session.commit()

    await send_message(token, chat_id, report_text)
    logger.info("scheduler/report: 리포트 전송 완료")


async def _run_startup(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """서버 기동 시 1회 실행: 패턴 캐시 동기화 + 일봉 백필."""
    from collector.daily_scheduler import (
        _active_markets,
        _backfill_market,
        _sync_pattern_cache,
    )

    async with session_factory() as session:
        await _sync_pattern_cache(session)
        markets = await _active_markets(session)
        for market in markets:
            try:
                await _backfill_market(session, settings, market)
            except Exception:
                logger.exception("scheduler/startup: backfill failed: %s", market)


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------

async def unified_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """통합 스케줄러 메인 루프.

    세 가지 태스크의 다음 실행 시각을 관리하고,
    가장 가까운 시각까지 sleep한 뒤 해당 태스크를 실행한다.
    """
    logger.info("unified_scheduler: starting (startup tasks)")
    try:
        await _run_startup(session_factory, settings)
    except Exception:
        logger.exception("unified_scheduler: startup failed, continuing")

    interval = settings.candle_interval_sec
    now = datetime.now(UTC)

    next_candle = now  # 즉시 첫 실행
    next_daily = _next_kst_target(0, 5)
    next_report = _next_report_target()

    logger.info(
        "unified_scheduler: started (candle=%.0fs, daily=00:05 KST, report=09/21 KST)",
        interval,
    )

    try:
        while True:
            now = datetime.now(UTC)
            nearest = min(next_candle, next_daily, next_report)
            wait = (nearest - now).total_seconds()

            if wait > 0:
                await asyncio.sleep(wait)

            now = datetime.now(UTC)

            if now >= next_candle:
                try:
                    await _tick_candle(session_factory, settings)
                except Exception:
                    logger.exception("unified_scheduler: candle tick error")
                next_candle = datetime.now(UTC) + timedelta(seconds=interval)

            if now >= next_daily:
                try:
                    await _tick_daily(session_factory, settings)
                except Exception:
                    logger.exception("unified_scheduler: daily tick error")
                next_daily = _next_kst_target(0, 5)

            if now >= next_report:
                try:
                    await _tick_report(session_factory, settings)
                except Exception:
                    logger.exception("unified_scheduler: report tick error")
                next_report = _next_report_target()

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("unified_scheduler stopped")

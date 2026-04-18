"""수집 스케줄러 — 캔들 수집, 일봉 수집/판정, 리포트 전송을 단일 루프로 관리.

가장 가까운 태스크까지 sleep → 실행 → 다음 시각 재계산.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select
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
    from services.candle_service import (
        _LABEL_TO_UNIT,
        active_markets,
        create_candle_jobs,
    )

    unit = settings.candle_unit
    count = settings.candle_count

    async with session_factory() as session:
        markets = await active_markets(session)
        if not markets:
            logger.debug("scheduler/candle: no active markets, skip")
            return

        created = await create_candle_jobs(session, markets, unit, count)
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
                    ref_created = await create_candle_jobs(
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
    """일봉 수집 Job 생성 + 패턴 적중률 판정 + 지표 튜닝 + Job 정리."""
    from core.strategy.tuning.pattern_cache import sync_pattern_cache
    from core.strategy.tuning.weight_tuning import update_indicator_weights
    from services.candle_service import active_markets, create_candle_jobs
    from services.job_cleanup import cleanup_old_jobs
    from services.pattern_service import evaluate_pending_signals

    async with session_factory() as session:
        markets = await active_markets(session)
        if markets:
            # 일봉 수집을 Job으로 (unit=1440 → /v1/candles/days)
            created = await create_candle_jobs(
                session, markets, unit=1440, count=2, ref_only=True,
            )
            logger.info(
                "scheduler/daily: created %d daily candle jobs", created,
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
                await sync_pattern_cache(session)
        except Exception:
            logger.exception("scheduler/daily: pattern evaluation failed")

        # 지표 가중치 적응형 갱신
        try:
            weight_result = await update_indicator_weights(session)
            logger.info("scheduler/daily: indicator weights %s", weight_result)
        except Exception:
            logger.exception("scheduler/daily: indicator weight update failed")

        # 탑다운 테이블 점수 DB 동기화
        try:
            from core.strategy.topdown_tables import sync_topdown_weights
            applied = await sync_topdown_weights(session)
            if applied > 0:
                logger.info("scheduler/daily: topdown scores synced (%d)", applied)
        except Exception:
            logger.exception("scheduler/daily: topdown weight sync failed")

        await cleanup_old_jobs(session)


async def _tick_report(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """리포트 생성 Job 생성."""
    from models.job import Job
    from models.job_status import JobStatus

    async with session_factory() as session:
        job = Job(
            type="generate_report",
            status=JobStatus.Ready.value,
            content={},
            priority=5,
        )
        session.add(job)
        await session.commit()
    logger.info("scheduler/report: generate_report job created")


async def _run_startup(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """서버 기동 시 1회 실행: 패턴 캐시 동기화 + 지표 캐시 + 일봉 백필."""
    from core.strategy.tuning.pattern_cache import sync_pattern_cache
    from models.indicator_weight import IndicatorWeight
    from services.candle_service import active_markets, backfill_market

    async def _sync_indicator_weights(sess: AsyncSession) -> None:
        """DB indicator_weights → 메모리 캐시 동기화."""
        from core.analysis.signal_weights import Weight, set_weights_cache_redis

        rows = (await sess.execute(
            select(IndicatorWeight)
        )).scalars().all()
        if not rows:
            return

        by_tf: dict[str, dict[str, Weight]] = {}
        for r in rows:
            if r.timeframe not in by_tf:
                by_tf[r.timeframe] = {}
            by_tf[r.timeframe][r.name] = Weight(
                r.name, r.direction, r.accuracy, r.is_active,
            )
        for tf, ws in by_tf.items():
            await set_weights_cache_redis(tf, ws)
        logger.info("indicator weights cache synced: %d records", len(rows))

    async with session_factory() as session:
        await sync_pattern_cache(session)
        await _sync_indicator_weights(session)

        # 탑다운 테이블 점수 DB 동기화
        try:
            from core.strategy.topdown_tables import sync_topdown_weights
            applied = await sync_topdown_weights(session)
            if applied > 0:
                logger.info("topdown scores synced on startup: %d", applied)
        except Exception:
            logger.exception("startup: topdown weight sync failed")

        markets = await active_markets(session)
        for market in markets:
            try:
                await backfill_market(session, settings, market)
            except Exception:
                logger.exception(
                    "scheduler/startup: backfill failed: %s", market,
                )


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------

async def collect_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """수집 스케줄러 메인 루프.

    세 가지 태스크의 다음 실행 시각을 관리하고,
    가장 가까운 시각까지 sleep한 뒤 해당 태스크를 실행한다.
    """
    logger.info("collect_scheduler: starting (startup tasks)")
    try:
        await _run_startup(session_factory, settings)
    except Exception:
        logger.exception("collect_scheduler: startup failed, continuing")

    interval = settings.candle_interval_sec
    now = datetime.now(UTC)

    next_candle = now  # 즉시 첫 실행
    next_daily = _next_kst_target(0, 5)
    next_report = _next_report_target()

    logger.info(
        "collect_scheduler: started "
        "(candle=%.0fs, daily=00:05 KST, report=09/21 KST)",
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
                    logger.exception("collect_scheduler: candle tick error")
                next_candle = datetime.now(UTC) + timedelta(seconds=interval)

            if now >= next_daily:
                try:
                    await _tick_daily(session_factory, settings)
                except Exception:
                    logger.exception("collect_scheduler: daily tick error")
                next_daily = _next_kst_target(0, 5)

            if now >= next_report:
                try:
                    await _tick_report(session_factory, settings)
                except Exception:
                    logger.exception("collect_scheduler: report tick error")
                next_report = _next_report_target()

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("collect_scheduler stopped")

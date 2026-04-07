"""캔들 수집 스케줄러 — 추적 자산에 대해 주기적으로 collect_candles Job 생성."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.job import Job
from models.job_status import JobStatus
from models.tracked_asset import TrackedAsset
from utils.config import Settings

logger = logging.getLogger(__name__)


async def _active_markets(session: AsyncSession) -> list[str]:
    """활성 TrackedAsset의 market 목록(중복 제거)."""
    result = await session.execute(
        select(TrackedAsset.market)
        .where(TrackedAsset.is_active.is_(True))
        .distinct()
    )
    return [row[0] for row in result.all()]


_LABEL_TO_UNIT = {
    "1m": 1, "3m": 3, "5m": 5, "10m": 10,
    "15m": 15, "30m": 30, "1h": 60, "4h": 240,
}


async def _create_candle_jobs(
    session: AsyncSession,
    markets: list[str],
    unit: int,
    count: int,
    ref_only: bool = False,
) -> int:
    """각 마켓에 대해 collect_candles Job 생성. 생성 개수 반환."""
    created = 0
    for market in markets:
        job = Job(
            type="collect_candles",
            status=JobStatus.Ready.value,
            content={
                "market": market, "unit": unit,
                "count": count, "ref_only": ref_only,
            },
            priority=10,
        )
        session.add(job)
        created += 1
    if created:
        await session.commit()
    return created


async def candle_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """주기적으로 활성 마켓의 캔들 수집 Job을 생성하는 무한 루프.

    수집 주기는 `AUTOCOIN_CANDLE_INTERVAL_SEC` (기본 60초 = 1분봉).
    각 사이클마다 최근 `AUTOCOIN_CANDLE_COUNT`개 캔들을 요청한다.
    """
    interval = settings.candle_interval_sec
    unit = settings.candle_unit
    count = settings.candle_count

    logger.info(
        "candle_scheduler: started (interval=%.1fs, unit=%dm, count=%d)",
        interval,
        unit,
        count,
    )

    try:
        while True:
            try:
                async with session_factory() as session:
                    markets = await _active_markets(session)
                    if not markets:
                        logger.debug("candle_scheduler: no active markets, skip")
                    else:
                        created = await _create_candle_jobs(session, markets, unit, count)
                        logger.info(
                            "candle_scheduler: created %d collect_candles jobs for %d markets",
                            created,
                            len(markets),
                        )
                        # 참조 타임프레임 수집 (추세 확인용, 분석 체이닝 안 함)
                        ref_tf_str = settings.candle_ref_timeframes
                        if ref_tf_str.strip():
                            for tf_label in ref_tf_str.split(","):
                                tf_label = tf_label.strip()
                                ref_unit = _LABEL_TO_UNIT.get(tf_label)
                                if ref_unit and ref_unit != unit:
                                    ref_created = await _create_candle_jobs(
                                        session, markets, ref_unit, count,
                                        ref_only=True,
                                    )
                                    logger.info(
                                        "candle_scheduler: created %d ref jobs (%s)",
                                        ref_created, tf_label,
                                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("candle_scheduler: error creating jobs")

            await asyncio.sleep(interval)
    finally:
        logger.info("candle_scheduler stopped")

"""캔들 수집 유틸 — 추적 자산 조회 및 collect_candles Job 생성.

스케줄 루프는 unified_scheduler.py에서 관리.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.job_status import JobStatus
from models.tracked_asset import TrackedAsset

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



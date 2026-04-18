"""Job 정리 유틸 — 완료/에러/멈춘 Job 삭제."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job

logger = logging.getLogger(__name__)

JOB_RETAIN_COMPLETE_DAYS = 3
JOB_RETAIN_ERROR_DAYS = 7


async def cleanup_old_jobs(session: AsyncSession) -> dict[str, int]:
    """오래된 Job 정리. Complete 3일, Error/Running(stuck) 7일."""
    now = datetime.now(UTC)
    counts: dict[str, int] = {}

    result = await session.execute(
        delete(Job).where(
            Job.status == "Complete",
            Job.updated_at < now - timedelta(days=JOB_RETAIN_COMPLETE_DAYS),
        )
    )
    counts["complete"] = result.rowcount  # type: ignore[assignment]

    result = await session.execute(
        delete(Job).where(
            Job.status == "Error",
            Job.updated_at < now - timedelta(days=JOB_RETAIN_ERROR_DAYS),
        )
    )
    counts["error"] = result.rowcount  # type: ignore[assignment]

    result = await session.execute(
        delete(Job).where(
            Job.status == "Running",
            Job.updated_at < now - timedelta(days=1),
        )
    )
    counts["stuck"] = result.rowcount  # type: ignore[assignment]

    total = sum(counts.values())
    if total > 0:
        await session.commit()
        logger.info(
            "job cleanup: %s deleted (complete=%d, error=%d, stuck=%d)",
            total, counts["complete"], counts["error"], counts["stuck"],
        )
    return counts

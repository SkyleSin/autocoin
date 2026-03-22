"""배치별 실제 처리 (빗썸 호출은 스레드로 넘겨 이벤트 루프 블로킹 방지)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bithumb.market import BithumbMarket
from bithumb.ticker import BithumbTicker
from models.job import Job
from models.job_status import JobStatus
from utils.config import Settings
from worker.batch import WorkBatch

logger = logging.getLogger(__name__)


def _markets_sync(settings: Settings) -> list[dict[str, Any]]:
    with BithumbMarket(settings) as c:
        return c.get_all()


def _ticker_sync(settings: Settings, markets: str) -> list[dict[str, Any]]:
    with BithumbTicker(settings) as c:
        return c.get(markets)


async def process_work_batch(session: AsyncSession, batch: WorkBatch, settings: Settings) -> None:
    result = await session.execute(select(Job).where(Job.id.in_(batch.job_ids)))
    jobs = list(result.scalars().all())
    by_id = {j.id: j for j in jobs}
    ordered = [by_id[i] for i in batch.job_ids if i in by_id]
    if len(ordered) != len(batch.job_ids):
        logger.warning(
            "batch job count mismatch: expected %s got %s",
            len(batch.job_ids),
            len(ordered),
        )

    try:
        if batch.job_type == "noop":
            for j in ordered:
                j.status = JobStatus.Complete.value
                j.response = {"ok": True}
        elif batch.job_type == "bithumb_markets":
            data = await asyncio.to_thread(_markets_sync, settings)
            for j in ordered:
                j.status = JobStatus.Complete.value
                j.response = {"markets": data}
        elif batch.job_type == "bithumb_ticker":
            markets = batch.merged.get("markets")
            if not markets:
                raise ValueError("bithumb_ticker 배치에 markets가 없습니다.")
            data = await asyncio.to_thread(_ticker_sync, settings, markets)
            pairs: list[tuple[Any, str]] = list(batch.merged.get("_pairs") or [])
            if pairs:
                by_market = {row.get("market"): row for row in data if isinstance(row, dict)}
                for job_id, mkt in pairs:
                    job = by_id.get(job_id)
                    if job is None:
                        continue
                    job.status = JobStatus.Complete.value
                    job.response = {"ticker": by_market.get(mkt)}
            else:
                for j in ordered:
                    j.status = JobStatus.Complete.value
                    j.response = {"tickers": data}
        else:
            raise ValueError(f"지원하지 않는 job type: {batch.job_type}")
        await session.commit()
    except Exception as e:
        logger.exception("work batch failed: %s", batch.job_type)
        await session.rollback()
        result2 = await session.execute(select(Job).where(Job.id.in_(batch.job_ids)))
        for j in result2.scalars().all():
            j.status = JobStatus.Error.value
            j.error_message = str(e)[:2000]
        await session.commit()

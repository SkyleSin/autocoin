"""DB에서 Ready/Failed 잡을 클레임해 `Queue`로 만든 뒤 `(priority, job_id)`를 우선순위 큐에 넣는다."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.job_service import JobService
from utils.config import Settings

logger = logging.getLogger(__name__)

JobQueueItem = tuple[int, uuid.UUID]

# 실제 DB job id와 겹치지 않도록 고정 UUID (워커 정상 종료 신호)
JOB_QUEUE_SHUTDOWN_UUID = uuid.UUID("6d95b1c2-5446-4f89-8888-000000000001")

SHUTDOWN_QUEUE_ITEM: JobQueueItem = (0, JOB_QUEUE_SHUTDOWN_UUID)


def is_shutdown_queue_item(item: JobQueueItem) -> bool:
    return item[1] == JOB_QUEUE_SHUTDOWN_UUID


async def poller(
    queue: asyncio.PriorityQueue[JobQueueItem],
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    try:
        while True:
            items: list[JobQueueItem] = []
            try:
                async with session_factory() as session:
                    svc = JobService(session)
                    claimed = await svc.claim_ready_jobs(settings.job_claim_limit)
                    items = [(j.priority, j.id) for j in claimed]
            except asyncio.CancelledError:
                logger.debug("poller cancelled")
                raise
            except Exception:
                logger.exception("poller: claim failed")
                await asyncio.sleep(settings.job_poll_interval_sec)
                continue

            if not items:
                logger.debug(
                    "poller: nothing to claim, sleep %.2fs",
                    settings.job_poll_interval_sec,
                )
                await asyncio.sleep(settings.job_poll_interval_sec)
                continue

            sample = [str(x[1])[:8] for x in items[:5]]
            suffix = " …" if len(items) > 5 else ""
            logger.info(
                "poller: enqueue start n=%s qsize~=%s job_id_prefixes=%s%s",
                len(items),
                queue.qsize(),
                sample,
                suffix,
            )
            for item in items:
                await queue.put(item)
            logger.info(
                "poller: enqueue done n=%s qsize~=%s",
                len(items),
                queue.qsize(),
            )
    finally:
        logger.info("poller stopped")

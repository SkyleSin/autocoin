"""DB `Ready` 잡 → 배치 → 큐 → N 워커. FastAPI `lifespan`에서 태스크로 기동."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.job_service import JobService
from utils.config import Settings
from worker.batch import WorkBatch, build_work_batches
from worker.handlers import process_work_batch
from worker.settings import WorkerSettings

logger = logging.getLogger(__name__)


async def _producer(
    queue: asyncio.Queue[WorkBatch],
    app_settings: Settings,
    w_settings: WorkerSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        while True:
            batches: list[WorkBatch] = []
            async with session_factory() as session:
                svc = JobService(session)
                claimed = await svc.claim_ready_jobs(w_settings.claim_limit)
                if claimed:
                    batches = build_work_batches(claimed)
            if not batches:
                await asyncio.sleep(w_settings.poll_interval_sec)
                continue
            for b in batches:
                await queue.put(b)
            logger.info(
                "queued %s batch(es)",
                len(batches),
            )
            await asyncio.sleep(w_settings.poll_interval_sec)
    except asyncio.CancelledError:
        logger.debug("worker producer cancelled")
        raise


async def _consumer(
    queue: asyncio.Queue[WorkBatch],
    app_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    name: str,
) -> None:
    try:
        while True:
            batch = await queue.get()
            try:
                async with session_factory() as session:
                    await process_work_batch(session, batch, app_settings)
            except Exception:
                logger.exception("consumer %s: unhandled", name)
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        logger.debug("worker consumer %s cancelled", name)
        raise


def spawn_worker_tasks(
    session_factory: async_sessionmaker[AsyncSession],
    app_settings: Settings,
    w_settings: WorkerSettings | None = None,
) -> tuple[asyncio.Queue[WorkBatch], list[asyncio.Task[None]]]:
    """프로듀서 1 + 컨슈머 N. 앱 종료 시 태스크 cancel + gather(return_exceptions=True)."""
    ws = w_settings or WorkerSettings()
    queue: asyncio.Queue[WorkBatch] = asyncio.Queue()
    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(
            _producer(queue, app_settings, ws, session_factory),
            name="job-worker-producer",
        ),
        *[
            asyncio.create_task(
                _consumer(queue, app_settings, session_factory, f"w{i}"),
                name=f"job-worker-consumer-{i}",
            )
            for i in range(ws.count)
        ],
    ]
    logger.info(
        "job workers spawned: consumers=%s poll=%ss claim_limit=%s",
        ws.count,
        ws.poll_interval_sec,
        ws.claim_limit,
    )
    return queue, tasks

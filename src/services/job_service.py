from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.job_status import JobStatus
from schemas.job import JobCreate, JobUpdate


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, body: JobCreate) -> Job:
        data: dict[str, Any] = body.model_dump(mode="json")
        row = Job(**data)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get(self, job_id: uuid.UUID) -> Job | None:
        return await self._session.get(Job, job_id)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> list[Job]:
        stmt = select(Job)
        if status is not None:
            stmt = stmt.where(Job.status == status)
        stmt = (
            stmt.order_by(Job.status.asc(), Job.priority.asc(), Job.id.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def claim_ready_jobs(self, limit: int) -> list[Job]:
        """`Ready` 행을 `SKIP LOCKED`으로 잡아 `Running`으로 바꾼 뒤, 같은 세션에서 다시 로드."""
        ids: list[uuid.UUID] = []
        async with self._session.begin():
            stmt = (
                select(Job)
                .where(Job.status == JobStatus.Ready.value)
                .order_by(Job.priority.asc(), Job.id.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            result = await self._session.execute(stmt)
            claimed = list(result.scalars().all())
            for job in claimed:
                job.status = JobStatus.Running.value
            ids = [j.id for j in claimed]
        if not ids:
            return []
        result2 = await self._session.execute(select(Job).where(Job.id.in_(ids)))
        return list(result2.scalars().all())

    async def update(self, job_id: uuid.UUID, body: JobUpdate) -> Job | None:
        row = await self.get(job_id)
        if row is None:
            return None
        patch = body.model_dump(exclude_unset=True, mode="json")
        for key, value in patch.items():
            setattr(row, key, value)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, job_id: uuid.UUID) -> bool:
        row = await self.get(job_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

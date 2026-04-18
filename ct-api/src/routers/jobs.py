from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from models.job_status import JobStatus
from schemas.job import JobCreate, JobRead, JobUpdate
from services.job_service import JobService
from utils.deps import get_job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreate,
    svc: Annotated[JobService, Depends(get_job_service)],
) -> JobRead:
    row = await svc.create(body)
    return JobRead.model_validate(row)


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: uuid.UUID,
    svc: Annotated[JobService, Depends(get_job_service)],
) -> JobRead:
    row = await svc.get(job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobRead.model_validate(row)


@router.get("", response_model=list[JobRead])
async def list_jobs(
    svc: Annotated[JobService, Depends(get_job_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[
        JobStatus | None,
        Query(alias="status", description="같은 status만 조회"),
    ] = None,
) -> list[JobRead]:
    st = status_filter.value if status_filter is not None else None
    rows = await svc.list(limit=limit, offset=offset, status=st)
    return [JobRead.model_validate(r) for r in rows]


@router.patch("/{job_id}", response_model=JobRead)
async def update_job(
    job_id: uuid.UUID,
    body: JobUpdate,
    svc: Annotated[JobService, Depends(get_job_service)],
) -> JobRead:
    row = await svc.update(job_id, body)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobRead.model_validate(row)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: uuid.UUID,
    svc: Annotated[JobService, Depends(get_job_service)],
) -> Response:
    ok = await svc.delete(job_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

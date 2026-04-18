"""Job API 래퍼."""

from __future__ import annotations

from infra.api_client import api_delete, api_get, api_patch, api_post


async def create_job(data: dict) -> dict:
    return await api_post("/api/jobs", json=data)


async def create_jobs_batch(jobs: list[dict]) -> dict:
    return await api_post("/api/jobs/batch", json=jobs)


async def claim_jobs(limit: int) -> list[dict]:
    return await api_post("/api/jobs/claim", json={"limit": limit})


async def update_job(job_id: int, data: dict) -> dict:
    return await api_patch(f"/api/jobs/{job_id}", json=data)


async def get_job(job_id: int) -> dict | None:
    return await api_get(f"/api/jobs/{job_id}")


async def cleanup_jobs(
    complete_days: int = 3, error_days: int = 7,
) -> dict:
    return await api_delete(
        "/api/jobs/cleanup",
        params={"complete_days": complete_days, "error_days": error_days},
    )

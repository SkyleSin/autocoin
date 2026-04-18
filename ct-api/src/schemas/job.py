from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from models.job_status import JobStatus


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., max_length=64)
    response: dict[str, Any] | None = None
    content: dict[str, Any] | None = None
    retry_cnt: int = 0
    error_message: str | None = None
    priority: int = 0


class JobUpdate(BaseModel):
    type: str | None = Field(default=None, max_length=64)
    status: JobStatus | None = None
    response: dict[str, Any] | None = None
    content: dict[str, Any] | None = None
    retry_cnt: int | None = None
    error_message: str | None = None
    priority: int | None = None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: JobStatus
    response: dict[str, Any] | None
    content: dict[str, Any] | None
    retry_cnt: int
    error_message: str | None
    priority: int
    created_at: datetime
    updated_at: datetime

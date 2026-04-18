from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base
from models.job_status import JobStatus


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_priority_id", "status", "priority", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=JobStatus.Ready.value,
    )
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    retry_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

"""TODO 메모 모델."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Todo(Base):
    """텔레그램으로 입력하는 TODO 메모."""

    __tablename__ = "todos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tag: Mapped[str] = mapped_column(
        String(32), nullable=False, default="general",
        comment="분류 태그 (general, bug, feature, question 등)",
    )
    done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

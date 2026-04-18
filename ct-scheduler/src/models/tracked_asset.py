from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class TrackedAsset(Base):
    """관심 코인·보유 코인 등 '추적 대상'."""

    __tablename__ = "tracked_assets"
    __table_args__ = (Index("ix_tracked_assets_market_intent", "market", "intent"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="watch",
        comment="예: watch, hold, portfolio",
    )
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

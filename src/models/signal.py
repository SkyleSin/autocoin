from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Signal(Base):
    """기술 지표 / LLM 판단 결과 시그널."""

    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_market_tf_created", "market", "timeframe", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    signal_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="BUY / SELL / HOLD",
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="0.0 ~ 1.0",
    )
    indicators: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="계산된 지표 스냅샷",
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="technical",
        comment="technical / local_llm / external_llm",
    )
    acted_on: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="매매 실행 여부",
    )

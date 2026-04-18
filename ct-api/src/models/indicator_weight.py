"""지표별 적응형 가중치 — 타임프레임별 적중률 기반 가중치 DB 관리."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class IndicatorWeight(Base):
    """지표별 적중률 기반 가중치."""

    __tablename__ = "indicator_weights"
    __table_args__ = (
        Index("ix_indicator_weights_key", "name", "direction", "timeframe", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="지표명 (rsi_overbought, bb_upper, macd_dead, sr_support_bounce 등)",
    )
    direction: Mapped[str] = mapped_column(
        String(8), nullable=False,
        comment="BUY / SELL",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False,
        comment="5m / 15m / 1h",
    )
    accuracy: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5,
        comment="적중률 (0.0~1.0)",
    )
    sample_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="누적 샘플 수",
    )
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="적중 건수",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="활성 여부 (50% 미만이면 False)",
    )

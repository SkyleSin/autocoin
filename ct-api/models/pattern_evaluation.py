"""패턴 일자별 판정 기록 — 시그널 발생 → N일 후 가격 비교 결과."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class PatternEvaluation(Base):
    """패턴 적중률 일자별 판정 결과."""

    __tablename__ = "pattern_evaluations"
    __table_args__ = (
        Index("ix_pat_eval_pattern_signal_date", "pattern_name", "signal_date"),
        Index("ix_pat_eval_eval_date", "eval_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    pattern_name: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="패턴 영문명 (예: bear_flag)",
    )
    signal_type: Mapped[str] = mapped_column(
        String(8), nullable=False,
        comment="BUY / SELL",
    )
    market: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )
    signal_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="시그널 발생일 (UTC)",
    )
    signal_price: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="시그널 시점 종가",
    )
    eval_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="판정일 (signal_date + eval_days)",
    )
    eval_price: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="판정일 종가",
    )
    pct_change: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="가격 변동률 (%)",
    )
    hit: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        comment="적중 여부 (BUY+상승=True, SELL+하락=True)",
    )
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="원본 Signal ID (참조용)",
    )

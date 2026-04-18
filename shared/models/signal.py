from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
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
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="적중률 검증 완료 여부",
    )
    version: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="1.0.1",
        comment="판단 기준 버전 (기준 변경 시 signals_bak으로 아카이빙)",
    )

    # ── MFE/MAE 트래킹 ──
    entry_price: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="시그널 생성 시점 종가",
    )
    exit_price: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="시그널 종료(매도) 시점 가격",
    )
    mfe: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Maximum Favorable Excursion (최대 유리 가격)",
    )
    mae: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Maximum Adverse Excursion (최대 불리 가격)",
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="시그널 종료 시각",
    )
    quality_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="MFE/MAE 기반 품질 점수 (0.0~1.0)",
    )

"""활성 알림 관리 — 시그널 중복 알림 방지 + 변경 감지."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ActiveAlert(Base):
    """현재 활성 상태인 시그널 알림. 마켓+타임프레임당 최대 1개."""

    __tablename__ = "active_alerts"
    __table_args__ = (
        Index("uq_active_alerts_market_tf", "market", "timeframe", unique=True),
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
        String(16), nullable=False,
        comment="BUY / SELL",
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    indicators: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_alerted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="마지막 텔레그램 알림 전송 시각",
    )
    passed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="사용자 패스 시각 (NULL이면 패스 안 함)",
    )
    passed_reasons: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None,
        comment="패스 당시 S/R 시그널+confidence 핑거프린트 (JSON). 시장 상황 변경 감지에 사용",
    )
    acted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="매매 실행 시각 (NULL이면 미실행). 동일 시그널 재알림 방지.",
    )

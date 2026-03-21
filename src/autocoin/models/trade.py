from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from autocoin.models.base import Base


class Trade(Base):
    """거래소에서 체결·주문한 사실을 기록 (감사·손익 추적용)."""

    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_market_created", "market", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="filled")
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    price_krw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    total_krw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    fee_krw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    exchange_fill_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

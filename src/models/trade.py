from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Trade(Base):
    """거래소에서 나온 체결·주문 등 거래 건별 기록 (워커가 적재)."""

    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_market_created", "market", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    price_krw: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

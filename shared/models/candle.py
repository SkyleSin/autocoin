from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Candle(Base):
    """OHLCV 캔들 데이터. (market, timeframe, open_at) 조합이 유니크."""

    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("market", "timeframe", "open_at", name="uq_candles_market_tf_open"),
        Index("ix_candles_market_tf_open", "market", "timeframe", "open_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="예: KRW-BTC",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="예: 1m, 5m, 15m, 1h, 1d",
    )
    open_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="봉 시작 시각 (UTC)",
    )
    open: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    volume: Mapped[Decimal] = mapped_column(
        Numeric(24, 8),
        nullable=False,
        comment="거래량 (코인 수량 기준)",
    )

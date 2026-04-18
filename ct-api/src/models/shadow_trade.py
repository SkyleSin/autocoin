"""섀도우 트레이딩 — 자동화 가정 시 가상 매매 기록."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Float, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ShadowTrade(Base):
    """가상 매매 기록 (자동화 시뮬레이션용)."""

    __tablename__ = "shadow_trades"
    __table_args__ = (
        Index("ix_shadow_trades_market_created", "market", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="BUY / SELL",
    )
    price: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, comment="체결 가격",
    )
    qty: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, comment="수량",
    )
    amount_krw: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, comment="총 금액 (KRW)",
    )
    fee: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, default=Decimal("0"), comment="수수료",
    )
    sell_pct: Mapped[int] = mapped_column(
        nullable=False, default=0, comment="매도 비율 (%, SELL만)",
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
    )
    pnl: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, default=Decimal("0"),
        comment="실현 손익 (SELL 시)",
    )
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, comment="원본 Signal ID",
    )
    version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="1.0.1",
    )


class ShadowPortfolio(Base):
    """가상 포트폴리오 상태 (마켓별)."""

    __tablename__ = "shadow_portfolio"
    __table_args__ = (
        Index("ix_shadow_portfolio_market", "market", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True,
    )
    qty: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, default=Decimal("0"),
        comment="보유 수량",
    )
    avg_price: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, default=Decimal("0"),
        comment="평균 매수가",
    )
    krw_balance: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, default=Decimal("1000000"),
        comment="가상 KRW 잔고 (전체 공유, market='_KRW'에 저장)",
    )

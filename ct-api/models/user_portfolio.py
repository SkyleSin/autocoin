"""유저별 보유 자산 포트폴리오."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class UserPortfolio(Base):
    """유저별 보유 코인/KRW 잔고.

    매매 체결 시 ct-worker가 업데이트하고,
    15분 주기로 빗썸 실제 잔고와 동기화.
    """

    __tablename__ = "user_portfolios"
    __table_args__ = (
        UniqueConstraint("user_id", "currency", name="uq_portfolio_user_currency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="KRW, BTC, ETH 등",
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, default=0,
        comment="보유 수량",
    )
    locked: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False, default=0,
        comment="주문 중 잠긴 수량",
    )
    avg_buy_price: Mapped[Decimal] = mapped_column(
        Numeric(24, 4), nullable=False, default=0,
        comment="평균 매입가 (KRW)",
    )

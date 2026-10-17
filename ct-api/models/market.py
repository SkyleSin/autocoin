"""빗썸 마켓 목록 — 주기적 동기화."""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Market(Base):
    """빗썸 KRW 마켓 목록. ct-worker가 주기적 동기화."""

    __tablename__ = "markets"

    market: Mapped[str] = mapped_column(
        String(32), primary_key=True,
        comment="KRW-BTC, KRW-ETH 등",
    )
    korean_name: Mapped[str] = mapped_column(String(64), nullable=False)
    english_name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class CoinMarket(Base):
    """거래소 마켓 단위 코인 메타 (심볼·이름·상태 등 조회용)."""

    __tablename__ = "coin_markets"
    __table_args__ = (UniqueConstraint("market", name="uq_coin_markets_market"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name_ko: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name_en: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

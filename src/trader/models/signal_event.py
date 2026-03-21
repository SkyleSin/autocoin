from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from trader.models.base import Base


class SignalEvent(Base):
    """외부 시스템이 보낸 '매매 의도' 로그.

    *거래소 체결 내역*은 `Trade` / `trades`에 넣고, *수집·분석 자료*는 `ResearchItem`에 둡니다.
    여기는 OpenCrawl 등이 HTTP로 넘긴 buy/sell/hold 같은 **신호만** 남깁니다 (감사·재처리용).
    """

    __tablename__ = "signal_events"

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
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="opencrawl")
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

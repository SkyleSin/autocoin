"""타임프레임별 지표 스냅샷 — 전략과 분석의 분리 경계."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class IndicatorSnapshot(Base):
    """마켓/타임프레임별 계산된 지표 스냅샷.

    봉 수집 후 compute_indicators에서 저장.
    전략은 이 테이블에서 필요한 TF의 최신 지표를 조회하여 판단.
    """

    __tablename__ = "indicator_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "market", "timeframe", "candle_open_at",
            name="uq_indicator_snapshot_market_tf_candle",
        ),
        Index(
            "ix_indicator_snapshot_market_tf_at",
            "market", "timeframe", "candle_open_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False,
        comment="1m, 15m, 1h, 4h, 1d",
    )
    candle_open_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="기준 캔들(최신)의 open_at",
    )
    indicators: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
        comment="compute_all + S/R + patterns 결과",
    )

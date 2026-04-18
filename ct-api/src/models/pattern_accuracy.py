"""패턴별 누적 적중률 — 실제 예측 정확도 추적."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class PatternAccuracy(Base):
    """패턴별 누적 적중률."""

    __tablename__ = "pattern_accuracies"

    pattern_name: Mapped[str] = mapped_column(
        String(64), primary_key=True,
        comment="패턴 영문 코드 (double_top, bull_flag 등)",
    )
    total_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="총 시그널 수",
    )
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="적중 수",
    )
    accuracy_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
        comment="적중률 (hit/total * 100)",
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="마지막 갱신 시각",
    )

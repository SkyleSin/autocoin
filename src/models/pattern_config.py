"""패턴 설정 — 활성/비활성, 적중률, 포지션 사이징을 DB로 관리."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class PatternConfig(Base):
    """패턴별 설정 + 집계 적중률."""

    __tablename__ = "pattern_configs"
    __table_args__ = (
        Index("ix_pattern_configs_name", "name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True,
        comment="패턴 영문명 (예: bear_flag)",
    )
    name_ko: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="패턴 한글명 (예: 하락깃발)",
    )
    signal: Mapped[str] = mapped_column(
        String(8), nullable=False,
        comment="BUY / SELL / HOLD",
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="활성 여부 (트리거로 사용)",
    )
    eval_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=7,
        comment="적중률 판정 기준일 (기본 7일)",
    )
    # 집계 적중률 (pattern_evaluations에서 계산)
    accuracy_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
        comment="누적 적중률 (%)",
    )
    total_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="총 판정 건수",
    )
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="적중 건수",
    )
    # 포지션 사이징
    buy_allocation_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=10.0,
        comment="매수 시 KRW 잔고 대비 기본 비율 (%)",
    )
    sell_recommendation_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=40,
        comment="매도 시 추천 비율 (%, 20단위)",
    )

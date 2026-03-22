from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class TrackedAsset(Base):
    """관심 코인·보유 코인 등 '추적 대상' 헤더. 상세 시계열·대용량 데이터는 `TrackedAssetDetail`."""

    __tablename__ = "tracked_assets"
    __table_args__ = (Index("ix_tracked_assets_market_intent", "market", "intent"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="watch",
        comment="예: watch, hold, portfolio",
    )
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    details: Mapped[list["TrackedAssetDetail"]] = relationship(  # noqa: UP037
        "TrackedAssetDetail",
        back_populates="tracked_asset",
        cascade="all, delete-orphan",
    )


class TrackedAssetDetail(Base):
    """추적 대상별 상세 스냅샷·원문·분석 결과 등 (행이 많아질 수 있음)."""

    __tablename__ = "tracked_asset_details"
    __table_args__ = (
        Index(
            "ix_tracked_asset_details_asset_created",
            "tracked_asset_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tracked_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracked_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    headline: Mapped[str | None] = mapped_column(String(256), nullable=True)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    detail_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)

    tracked_asset: Mapped[TrackedAsset] = relationship(back_populates="details")

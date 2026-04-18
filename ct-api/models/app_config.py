"""앱 설정 key-value 저장소 — 시스템 전역 + 유저별 설정 통합.

user_id=NULL → 시스템 기본값
user_id=N    → 유저별 오버라이드
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class AppConfig(Base):
    """key-value 설정 테이블 (시스템 전역 + 유저별)."""

    __tablename__ = "app_configs"
    __table_args__ = (
        UniqueConstraint("key", "user_id", name="uq_app_config_key_user"),
    )

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, primary_key=True, default=0,
        comment="0=시스템 전역, N=유저별 오버라이드",
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)

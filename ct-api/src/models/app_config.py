"""앱 설정 key-value 저장소 — 서버 재시작 후에도 유지되는 런타임 설정."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class AppConfig(Base):
    """key-value 설정 테이블."""

    __tablename__ = "app_configs"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

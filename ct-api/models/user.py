"""유저 모델."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class User(Base):
    """등록된 유저. 유저별 거래소 인증."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False,
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)

    # 거래소 인증 (암호화 저장)
    bithumb_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    bithumb_secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    coin_watchlist: Mapped[list] = mapped_column(JSONB, default=list)

    risk_settings: Mapped[dict] = mapped_column(
        JSONB, default=lambda: {
            "max_per_trade_pct": 15.0,
            "max_daily_trades": 30,
            "min_krw_reserve": 100_000,
        },
        comment="유저별 리스크 설정 (없으면 기본값 사용)",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

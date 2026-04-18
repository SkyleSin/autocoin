"""유저 모델."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class User(Base):
    """등록된 유저. 유저별 거래소 인증 + 전략/리스크 설정."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False,
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)

    # 거래소 인증 (암호화 저장)
    bithumb_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    bithumb_secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 전략 설정
    strategy_type: Mapped[str] = mapped_column(
        String(32), default="topdown_mtf",
    )
    strategy_params: Mapped[dict] = mapped_column(JSONB, default=dict)
    coin_watchlist: Mapped[list] = mapped_column(JSONB, default=list)

    # 리스크 설정
    risk_max_per_trade_pct: Mapped[float] = mapped_column(
        Float, default=5.0,
    )
    risk_max_daily_trades: Mapped[int] = mapped_column(Integer, default=10)
    risk_min_krw_reserve: Mapped[float] = mapped_column(
        Float, default=100000,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

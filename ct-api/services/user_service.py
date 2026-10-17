"""유저 관련 서비스."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from utils.config import Settings
from utils.crypto import decrypt


async def get_active_users(session: AsyncSession) -> list[User]:
    """활성 유저 목록 조회."""
    result = await session.execute(
        select(User).where(User.is_active.is_(True))
    )
    return list(result.scalars().all())


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_id: int,
) -> User | None:
    """텔레그램 ID로 유저 조회."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


def get_user_settings(user: User, base_settings: Settings) -> Settings:
    """유저별 Settings 복사본. API 키와 리스크 설정을 유저 값으로 오버라이드."""
    encrypt_key = base_settings.encrypt_key
    overrides: dict = {}

    if user.bithumb_api_key:
        overrides["bithumb_api_key"] = decrypt(
            user.bithumb_api_key, encrypt_key,
        )
    if user.bithumb_secret_key:
        overrides["bithumb_secret_key"] = decrypt(
            user.bithumb_secret_key, encrypt_key,
        )

    overrides["risk_max_per_trade_pct"] = user.risk_max_per_trade_pct
    overrides["risk_max_daily_trades"] = user.risk_max_daily_trades
    overrides["risk_min_krw_reserve"] = user.risk_min_krw_reserve

    return base_settings.model_copy(update=overrides)


def get_user_settings_from_dict(
    user_data: dict, base_settings: Settings,
) -> Settings:
    """API 응답(dict)에서 유저별 Settings 생성. ct-worker용."""
    encrypt_key = base_settings.encrypt_key
    overrides: dict = {}

    api_key = user_data.get("bithumb_api_key", "")
    secret_key = user_data.get("bithumb_secret_key", "")
    if api_key:
        overrides["bithumb_api_key"] = decrypt(api_key, encrypt_key)
    if secret_key:
        overrides["bithumb_secret_key"] = decrypt(secret_key, encrypt_key)

    if "risk_max_per_trade_pct" in user_data:
        overrides["risk_max_per_trade_pct"] = user_data["risk_max_per_trade_pct"]
    if "risk_max_daily_trades" in user_data:
        overrides["risk_max_daily_trades"] = user_data["risk_max_daily_trades"]
    if "risk_min_krw_reserve" in user_data:
        overrides["risk_min_krw_reserve"] = user_data["risk_min_krw_reserve"]

    return base_settings.model_copy(update=overrides)

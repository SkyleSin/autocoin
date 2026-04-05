"""앱 설정 DB CRUD — 리스크 설정 등 런타임 설정 관리."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from models.app_config import AppConfig
from utils.config import Settings

# DB에 저장할 리스크 설정 키
RISK_KEYS = {
    "risk_max_per_trade_pct": float,
    "risk_max_daily_trades": int,
    "risk_min_krw_reserve": float,
}


async def get_config(session: AsyncSession, key: str) -> str | None:
    """설정값 조회."""
    row = await session.get(AppConfig, key)
    return row.value if row else None


async def set_config(session: AsyncSession, key: str, value: str) -> None:
    """설정값 저장 (upsert)."""
    row = await session.get(AppConfig, key)
    if row:
        row.value = value
    else:
        session.add(AppConfig(key=key, value=value))
    await session.commit()


async def load_risk_settings(
    session: AsyncSession, settings: Settings,
) -> dict[str, str]:
    """DB에서 리스크 설정을 로드하여 Settings에 반영. 변경된 항목 반환."""
    changed: dict[str, str] = {}
    for key, cast in RISK_KEYS.items():
        val = await get_config(session, key)
        if val is not None:
            try:
                parsed = cast(val)
                current = getattr(settings, key)
                if current != parsed:
                    setattr(settings, key, parsed)
                    changed[key] = val
            except (ValueError, TypeError):
                pass
    return changed


async def save_risk_setting(
    session: AsyncSession,
    settings: Settings,
    key: str,
    value: float,
) -> bool:
    """리스크 설정 하나를 DB에 저장하고 Settings에 반영."""
    if key not in RISK_KEYS:
        return False
    cast = RISK_KEYS[key]
    setattr(settings, key, cast(value))
    await set_config(session, key, str(value))
    return True

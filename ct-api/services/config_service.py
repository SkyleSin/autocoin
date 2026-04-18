"""앱 설정 DB CRUD — 시스템 전역 + 유저별 리스크 설정 관리."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.app_config import AppConfig
from utils.config import Settings

# DB에 저장할 런타임 설정 키
DB_KEYS: dict[str, type] = {
    "risk_max_per_trade_pct": float,
    "risk_max_daily_trades": int,
    "risk_min_krw_reserve": float,
    "alert_remind_interval_sec": float,
}

RISK_KEYS = DB_KEYS


async def get_config(
    session: AsyncSession, key: str, *, user_id: int = 0,
) -> str | None:
    """설정값 조회. user_id별 오버라이드 → 시스템 기본값 순서."""
    if user_id != 0:
        row = await session.get(AppConfig, (key, user_id))
        if row:
            return row.value
    # 시스템 기본값
    row = await session.get(AppConfig, (key, 0))
    return row.value if row else None


async def set_config(
    session: AsyncSession, key: str, value: str, *, user_id: int = 0,
) -> None:
    """설정값 저장 (upsert)."""
    row = await session.get(AppConfig, (key, user_id))
    if row:
        row.value = value
    else:
        session.add(AppConfig(key=key, user_id=user_id, value=value))
    await session.commit()


async def get_user_risk_settings(
    session: AsyncSession, user_id: int,
) -> dict[str, float | int]:
    """유저별 리스크 설정 조회 (오버라이드 + 시스템 기본값 병합)."""
    result: dict[str, float | int] = {}
    for key, cast in RISK_KEYS.items():
        val = await get_config(session, key, user_id=user_id)
        if val is not None:
            try:
                result[key] = cast(val)
            except (ValueError, TypeError):
                pass
    return result


async def load_risk_settings(
    session: AsyncSession, settings: Settings,
) -> dict[str, str]:
    """DB 시스템 전역 설정 → Settings에 반영. 변경된 항목 반환."""
    changed: dict[str, str] = {}
    for key, cast in RISK_KEYS.items():
        val = await get_config(session, key, user_id=0)
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
    *,
    user_id: int = 0,
) -> bool:
    """리스크 설정 저장. user_id=0이면 시스템 전역 + Settings 반영."""
    if key not in RISK_KEYS:
        return False
    await set_config(session, key, str(value), user_id=user_id)
    if user_id == 0:
        cast = RISK_KEYS[key]
        setattr(settings, key, cast(value))
    return True

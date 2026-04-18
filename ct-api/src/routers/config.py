"""AppConfig 라우터 — key-value 설정 + 리스크 설정 일괄 로드."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.config_service import (
    DB_KEYS,
    get_config,
    load_risk_settings,
    set_config,
)
from utils.deps import get_db

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigValue(BaseModel):
    value: str


@router.get("/risk")
async def risk_settings(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """리스크 설정 일괄 로드 (DB값 + Settings 현재값)."""
    settings = request.app.state.settings
    changed = await load_risk_settings(db, settings)
    result: dict[str, Any] = {}
    for key in DB_KEYS:
        result[key] = getattr(settings, key, None)
    result["changed"] = changed
    return result


@router.get("/{key}")
async def get(
    key: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    val = await get_config(db, key)
    if val is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return {"key": key, "value": val}


@router.put("/{key}")
async def put(
    key: str,
    body: ConfigValue,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    await set_config(db, key, body.value)
    return {"key": key, "value": body.value}

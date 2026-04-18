"""IndicatorWeight API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_post


async def get_indicator_weights(
    timeframe: str | None = None,
    is_active: bool | None = None,
) -> list[dict]:
    params: dict = {}
    if timeframe:
        params["timeframe"] = timeframe
    if is_active is not None:
        params["is_active"] = is_active
    return await api_get("/api/indicator-weights", params=params)


async def upsert_indicator_weight(data: dict) -> dict:
    return await api_post("/api/indicator-weights/upsert", json=data)

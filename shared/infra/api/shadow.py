"""Shadow API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_post


async def record_shadow_trade(data: dict) -> dict | None:
    return await api_post("/api/shadow/trade", json=data)


async def get_shadow_summary() -> dict:
    return await api_get("/api/shadow/summary")

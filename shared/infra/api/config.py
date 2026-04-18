"""Config API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_put


async def get_config(key: str) -> dict | None:
    return await api_get(f"/api/config/{key}")


async def set_config(key: str, value: str) -> dict:
    return await api_put(f"/api/config/{key}", json={"value": value})


async def get_risk_config() -> dict:
    return await api_get("/api/config/risk")

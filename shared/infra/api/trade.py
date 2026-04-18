"""Trade API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_post


async def create_trade(data: dict) -> dict:
    return await api_post("/api/trades", json=data)


async def get_trades(limit: int = 10) -> list[dict]:
    return await api_get("/api/trades", params={"limit": limit})


async def get_daily_trade_count() -> int:
    r = await api_get("/api/trades/daily-count")
    return r["count"]

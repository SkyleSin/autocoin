"""Trade API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_post


async def create_trade(data: dict) -> dict:
    return await api_post("/api/trades", json=data)


async def get_trades(limit: int = 10, user_id: int | None = None) -> list[dict]:
    params: dict = {"limit": limit}
    if user_id is not None:
        params["user_id"] = user_id
    return await api_get("/api/trades", params=params)


async def get_daily_trade_count(user_id: int | None = None) -> int:
    params = {}
    if user_id is not None:
        params["user_id"] = user_id
    r = await api_get("/api/trades/daily-count", params=params)
    return r["count"]

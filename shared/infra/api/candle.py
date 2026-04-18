"""Candle API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_post


async def upsert_candles(candles: list[dict]) -> dict:
    return await api_post("/api/candles/upsert", json={"candles": candles})


async def get_candles(
    market: str,
    timeframe: str,
    limit: int = 200,
    order: str = "desc",
) -> list[dict]:
    return await api_get(
        "/api/candles",
        params={
            "market": market, "timeframe": timeframe,
            "limit": limit, "order": order,
        },
    )


async def get_candle_count(market: str, timeframe: str) -> int:
    r = await api_get(
        "/api/candles/count",
        params={"market": market, "timeframe": timeframe},
    )
    return r["count"]

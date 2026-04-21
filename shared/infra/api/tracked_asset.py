"""TrackedAsset API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_patch, api_post, api_put


async def get_active_markets() -> list[dict]:
    return await api_get("/api/tracked-assets/active")


async def add_tracked_asset(market: str, intent: str = "") -> dict:
    return await api_post(
        "/api/tracked-assets", json={"market": market, "intent": intent},
    )


async def update_tracked_asset(market: str, **kwargs: object) -> dict:
    return await api_patch(f"/api/tracked-assets/{market}", json=kwargs)


async def update_market_prices(items: list[dict]) -> dict:
    """현재가 일괄 업데이트. items: [{"market": "KRW-BTC", "price": 12345.0}, ...]"""
    return await api_put("/api/tracked-assets/prices", json=items)

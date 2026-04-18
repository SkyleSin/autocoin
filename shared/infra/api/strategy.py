"""Strategy / Risk API 래퍼."""

from __future__ import annotations

from infra.api_client import api_post


async def evaluate_patterns(
    pattern_markets: list[str] | None = None,
) -> dict:
    return await api_post(
        "/api/strategy/evaluate-patterns",
        json={"pattern_markets": pattern_markets},
    )


async def update_weights() -> dict:
    return await api_post("/api/strategy/update-weights")


async def sync_pattern_cache() -> dict:
    return await api_post("/api/strategy/sync-pattern-cache")


async def sync_topdown_weights() -> dict:
    return await api_post("/api/strategy/sync-topdown-weights")


async def check_risk(data: dict) -> dict:
    return await api_post("/api/risk/check", json=data)

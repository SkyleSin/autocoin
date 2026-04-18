"""IndicatorSnapshot API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_post


async def upsert_indicator_snapshot(data: dict) -> dict:
    return await api_post("/api/indicator-snapshots/upsert", json=data)


async def get_latest_snapshot(
    market: str, timeframe: str,
) -> dict | None:
    """최신 지표 스냅샷의 indicators dict 반환."""
    r = await api_get(
        "/api/indicator-snapshots/latest",
        params={"market": market, "timeframe": timeframe},
    )
    if not r or not isinstance(r, dict):
        return None
    return r.get("indicators")

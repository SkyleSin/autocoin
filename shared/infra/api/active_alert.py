"""ActiveAlert API 래퍼 — CRUD 전용."""

from __future__ import annotations

from infra.api_client import api_delete, api_get, api_patch, api_post


async def find_alert(
    market: str, timeframe: str, *, user_id: int | None = None,
) -> dict | None:
    params: dict = {"market": market, "timeframe": timeframe}
    if user_id is not None:
        params["user_id"] = user_id
    return await api_get("/api/active-alerts/find", params=params)


async def create_alert(data: dict) -> dict:
    return await api_post("/api/active-alerts", json=data)


async def update_alert(
    market: str, timeframe: str, updates: dict, *, user_id: int | None = None,
) -> dict:
    return await api_patch("/api/active-alerts/update", json={
        "market": market,
        "timeframe": timeframe,
        "user_id": user_id,
        "updates": updates,
    })


async def mark_alert_passed(market: str, *, user_id: int | None = None) -> dict:
    payload: dict = {"market": market}
    if user_id is not None:
        payload["user_id"] = user_id
    return await api_post("/api/active-alerts/mark-passed", json=payload)


async def mark_alert_acted(market: str, *, user_id: int | None = None) -> dict:
    payload: dict = {"market": market}
    if user_id is not None:
        payload["user_id"] = user_id
    return await api_patch("/api/active-alerts/mark-acted", json=payload)


async def delete_alerts(market: str, timeframe: str) -> None:
    await api_delete(
        "/api/active-alerts/remove",
        params={"market": market, "timeframe": timeframe},
    )

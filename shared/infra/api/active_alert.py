"""ActiveAlert API 래퍼."""

from __future__ import annotations

from infra.api_client import api_delete, api_patch, api_post


async def check_alert(data: dict) -> dict:
    return await api_post("/api/active-alerts/check", json=data)


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
        "/api/active-alerts",
        params={"market": market, "timeframe": timeframe},
    )

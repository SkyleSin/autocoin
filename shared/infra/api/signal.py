"""Signal API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_patch, api_post


async def create_signal(data: dict) -> dict:
    return await api_post("/api/signals", json=data)


async def get_latest_signal(
    market: str, limit: int = 1,
) -> dict | None:
    """최신 시그널 1개 반환. 없으면 None."""
    r = await api_get(
        "/api/signals/latest",
        params={"market": market, "limit": limit},
    )
    if isinstance(r, list):
        return r[0] if r else None
    return r if r else None


async def update_signal(signal_id: int, data: dict) -> dict:
    return await api_patch(f"/api/signals/{signal_id}", json=data)


async def close_opposite_signals(
    market: str, new_type: str, exit_price: float,
) -> dict:
    return await api_post(
        "/api/signals/close-opposite",
        json={
            "market": market,
            "new_signal_type": new_type,
            "exit_price": exit_price,
        },
    )


async def get_open_signals(
    market: str, types: str = "BUY,SELL",
) -> list[dict]:
    return await api_get(
        "/api/signals/open",
        params={"market": market, "types": types},
    )

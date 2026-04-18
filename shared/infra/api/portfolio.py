"""Portfolio API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get


async def get_user_portfolio(user_id: int) -> list[dict]:
    """유저의 보유 자산 목록 조회."""
    return await api_get(f"/api/portfolios/{user_id}") or []


async def get_user_currency(user_id: int, currency: str) -> dict | None:
    """유저의 특정 코인 보유량 조회."""
    return await api_get(f"/api/portfolios/{user_id}/{currency}")

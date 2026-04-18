"""Market API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get


async def get_markets(keyword: str = "") -> list[dict]:
    """KRW 마켓 목록 조회. keyword로 필터."""
    params = {}
    if keyword:
        params["keyword"] = keyword
    return await api_get("/api/markets", params=params) or []

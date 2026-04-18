"""User API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get


async def get_active_users() -> list[dict]:
    return await api_get("/api/users/active")


async def get_user_by_telegram(telegram_id: int) -> dict | None:
    """텔레그램 ID로 유저 조회. 미등록 시 None."""
    import httpx
    try:
        return await api_get(f"/api/users/by-telegram/{telegram_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


async def get_user(user_id: int) -> dict | None:
    return await api_get(f"/api/users/{user_id}")


async def get_user_full(user_id: int) -> dict | None:
    """내부 서비스 전용 — 암호화된 API 키 포함."""
    return await api_get(f"/api/users/{user_id}/full")

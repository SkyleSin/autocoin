"""ct-api REST 클라이언트 — 다른 서비스에서 DB 접근용."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_base_url: str = ""
_client: httpx.AsyncClient | None = None


async def init_api_client(base_url: str) -> None:
    """API 클라이언트 초기화. 각 엔트리포인트 startup에서 호출."""
    global _base_url, _client  # noqa: PLW0603
    _base_url = base_url.rstrip("/")
    _client = httpx.AsyncClient(base_url=_base_url, timeout=30.0)
    logger.info("API 클라이언트 초기화: %s", _base_url)


async def close_api_client() -> None:
    """API 클라이언트 종료."""
    global _client  # noqa: PLW0603
    if _client:
        await _client.aclose()
        _client = None
        logger.info("API 클라이언트 종료")


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("API 클라이언트 미초기화 — init_api_client() 먼저 호출")
    return _client


async def api_get(path: str, params: dict | None = None) -> dict | list | None:
    """GET 요청."""
    r = await _get_client().get(path, params=params)
    r.raise_for_status()
    return r.json()


async def api_post(path: str, json: dict | None = None) -> dict | list | None:
    """POST 요청."""
    r = await _get_client().post(path, json=json)
    r.raise_for_status()
    return r.json()


async def api_patch(path: str, json: dict | None = None) -> dict | list | None:
    """PATCH 요청."""
    r = await _get_client().patch(path, json=json)
    r.raise_for_status()
    return r.json()


async def api_put(path: str, json: dict | None = None) -> dict | list | None:
    """PUT 요청."""
    r = await _get_client().put(path, json=json)
    r.raise_for_status()
    return r.json()


async def api_delete(path: str, params: dict | None = None) -> None:
    """DELETE 요청."""
    r = await _get_client().delete(path, params=params)
    r.raise_for_status()

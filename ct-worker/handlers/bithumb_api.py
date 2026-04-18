"""빗썸 API 동기 호출 함수 모음 — asyncio.to_thread로 실행."""

from __future__ import annotations

import logging
from typing import Any

from utils.config import Settings
from workers.private_requester import PrivateRequester
from workers.public_requester import PublicRequester

logger = logging.getLogger(__name__)


class BithumbAPIError(Exception):
    pass


def get_accounts(settings: Settings) -> list[dict[str, Any]]:
    """빗썸 잔고 조회."""
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/accounts")
    if not isinstance(data, list):
        raise BithumbAPIError(f"accounts 응답 오류: {data}")
    return data


def place_order(
    settings: Settings,
    market: str,
    side: str,
    *,
    volume: str | None = None,
    price: str | None = None,
    ord_type: str = "limit",
) -> dict[str, Any]:
    """빗썸 주문 실행."""
    params: dict[str, Any] = {"market": market, "side": side, "ord_type": ord_type}
    if volume is not None:
        params["volume"] = volume
    if price is not None:
        params["price"] = price
    with PrivateRequester(settings) as prv:
        data = prv.request("POST", "/v1/orders", json_body=params)
    if not isinstance(data, dict):
        raise BithumbAPIError(f"orders 응답 오류: {data}")
    return data


def get_order(settings: Settings, uuid: str) -> dict[str, Any]:
    """빗썸 주문 조회."""
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/order", params={"uuid": uuid})
    if not isinstance(data, dict):
        raise BithumbAPIError(f"order 조회 오류: {data}")
    return data


def cancel_order(settings: Settings, uuid: str) -> dict[str, Any]:
    """빗썸 주문 취소."""
    with PrivateRequester(settings) as prv:
        data = prv.request("DELETE", "/v1/order", json_body={"uuid": uuid})
    if not isinstance(data, dict):
        raise BithumbAPIError(f"cancel 응답 오류: {data}")
    return data


def get_ticker(settings: Settings, markets: str) -> list[dict[str, Any]]:
    """빗썸 현재가 조회."""
    with PublicRequester(settings) as pub:
        data = pub.request("GET", "/v1/ticker", params={"markets": markets})
    if not isinstance(data, list):
        raise BithumbAPIError(f"ticker 응답 오류: {data}")
    return data

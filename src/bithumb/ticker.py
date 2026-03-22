from __future__ import annotations

from typing import Any

from bithumb.base import BithumbClientBase


class BithumbTicker(BithumbClientBase):
    """현재가 등 티커."""

    def get(self, markets: str) -> list[dict[str, Any]]:
        """`markets`: 콤마 구분 (예: `KRW-BTC,KRW-ETH`)."""
        data = self.public_request("GET", "/v1/ticker", params={"markets": markets})
        if not isinstance(data, list):
            msg = "ticker 응답이 리스트가 아닙니다."
            raise TypeError(msg)
        return data

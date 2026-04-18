from __future__ import annotations

from typing import Any

from bithumb.base import BithumbClientBase


class BithumbMarket(BithumbClientBase):
    """마켓·종목 코드 조회."""

    def get_all(self) -> list[dict[str, Any]]:
        data = self.public_request("GET", "/v1/market/all")
        if not isinstance(data, list):
            msg = "market/all 응답이 리스트가 아닙니다."
            raise TypeError(msg)
        return data

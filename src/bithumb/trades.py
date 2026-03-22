from __future__ import annotations

from typing import Any

from bithumb.base import BithumbClientBase


class BithumbTrades(BithumbClientBase):
    """최근 체결(틱) 등 공개 체결 데이터."""

    def get_ticks(
        self,
        market: str,
        *,
        count: int | None = None,
        to: str | None = None,
    ) -> list[dict[str, Any]]:
        """`GET /v1/trades/ticks` — 문서 변경 시 경로·파라미터 확인."""
        params: dict[str, Any] = {"market": market}
        if count is not None:
            params["count"] = count
        if to is not None:
            params["to"] = to
        data = self.public_request("GET", "/v1/trades/ticks", params=params)
        if not isinstance(data, list):
            msg = "trades/ticks 응답이 리스트가 아닙니다."
            raise TypeError(msg)
        return data

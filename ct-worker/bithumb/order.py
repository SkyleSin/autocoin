"""빗썸 주문 API — POST /v1/orders, GET /v1/order, DELETE /v1/order."""

from __future__ import annotations

from typing import Any

from bithumb.base import BithumbClientBase


class BithumbOrder(BithumbClientBase):
    """주문 실행/조회/취소."""

    def place(
        self,
        market: str,
        side: str,
        *,
        volume: str | None = None,
        price: str | None = None,
        ord_type: str = "limit",
    ) -> dict[str, Any]:
        """주문 실행.

        Args:
            market: 마켓 코드 (예: "KRW-BTC")
            side: "bid"(매수) 또는 "ask"(매도)
            volume: 주문 수량 (매도 시 필수)
            price: 주문 가격/금액 (매수 시 필수)
            ord_type: "limit"(지정가), "price"(시장가 매수), "market"(시장가 매도)
        """
        params: dict[str, Any] = {
            "market": market,
            "side": side,
            "ord_type": ord_type,
        }
        if volume is not None:
            params["volume"] = volume
        if price is not None:
            params["price"] = price
        data = self.private_request("POST", "/v1/orders", json_body=params)
        if not isinstance(data, dict):
            msg = "orders 응답이 객체가 아닙니다."
            raise TypeError(msg)
        return data

    def get(self, uuid: str) -> dict[str, Any]:
        """개별 주문 조회."""
        data = self.private_request("GET", "/v1/order", params={"uuid": uuid})
        if not isinstance(data, dict):
            msg = "order 조회 응답이 객체가 아닙니다."
            raise TypeError(msg)
        return data

    def cancel(self, uuid: str) -> dict[str, Any]:
        """주문 취소."""
        data = self.private_request("DELETE", "/v1/order", json_body={"uuid": uuid})
        if not isinstance(data, dict):
            msg = "order 취소 응답이 객체가 아닙니다."
            raise TypeError(msg)
        return data

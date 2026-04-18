"""빗썸 잔고 조회 — GET /v1/accounts."""

from __future__ import annotations

from typing import Any

from bithumb.base import BithumbClientBase


class BithumbAccount(BithumbClientBase):
    """계좌 잔고 조회."""

    def get_balances(self) -> list[dict[str, Any]]:
        """전체 잔고 조회. 보유 중인 자산 목록 반환."""
        data = self.private_request("GET", "/v1/accounts")
        if not isinstance(data, list):
            msg = "accounts 응답이 리스트가 아닙니다."
            raise TypeError(msg)
        return data

    def get_balance(self, currency: str) -> dict[str, Any] | None:
        """특정 통화 잔고 조회. 예: currency='BTC' 또는 'KRW'."""
        balances = self.get_balances()
        currency_upper = currency.upper()
        for b in balances:
            if b.get("currency", "").upper() == currency_upper:
                return b
        return None

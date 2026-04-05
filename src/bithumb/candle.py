"""빗썸 캔들(시세) 조회 — GET /v1/candles/minutes/{unit}."""

from __future__ import annotations

from typing import Any

from bithumb.base import BithumbClientBase


class BithumbCandle(BithumbClientBase):
    """분봉·일봉 캔들 데이터 조회."""

    def get_minutes(
        self,
        unit: int,
        market: str,
        *,
        count: int = 1,
        to: str | None = None,
    ) -> list[dict[str, Any]]:
        """분봉 캔들 조회.

        Args:
            unit: 분 단위 (1, 3, 5, 10, 15, 30, 60, 240)
            market: 마켓 코드 (예: "KRW-BTC")
            count: 캔들 개수 (최대 200)
            to: 마지막 캔들 시각 (ISO 8601, 예: "2024-01-01T00:00:00")
        """
        params: dict[str, Any] = {"market": market, "count": count}
        if to is not None:
            params["to"] = to
        data = self.public_request("GET", f"/v1/candles/minutes/{unit}", params=params)
        if not isinstance(data, list):
            msg = f"candles/minutes/{unit} 응답이 리스트가 아닙니다."
            raise TypeError(msg)
        return data

"""인증 없이 호출 가능한 공개 REST API."""

from __future__ import annotations

from typing import Any

from .base import CoinServerClientBase


class PublicRequester(CoinServerClientBase):
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        r = self._client.request(method.upper(), path, params=params)
        return self._parse_response(r)

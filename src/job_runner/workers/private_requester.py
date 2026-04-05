"""API Key / 서명이 필요한 비공개 REST API."""

from __future__ import annotations

from typing import Any

from bithumb.auth import build_authorization_header

from .base import CoinServerClientBase


class PrivateRequester(CoinServerClientBase):
    def _require_keys(self) -> tuple[str, str]:
        k, s = self._settings.bithumb_api_key, self._settings.bithumb_secret_key
        if not k or not s:
            msg = "Private API에는 BITHUMB_API_KEY, BITHUMB_SECRET_KEY가 필요합니다."
            raise ValueError(msg)
        return k, s

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        access, secret = self._require_keys()
        m = method.upper()

        # query_hash 계산용 파라미터
        hash_params: dict[str, Any] | None = None
        if params:
            hash_params = params
        if json_body is not None:
            hash_params = json_body

        auth = build_authorization_header(access, secret, params=hash_params)
        headers = {"Authorization": auth}

        if m == "GET":
            r = self._client.request(m, path, params=params, headers=headers)
        elif json_body is not None:
            # POST + JSON body
            headers["Content-Type"] = "application/json; charset=utf-8"
            r = self._client.request(
                m, path, headers=headers, json=json_body,
            )
        elif params is not None:
            # POST + form data (빗썸 주문 등)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            r = self._client.request(
                m, path, headers=headers, data=params,
            )
        else:
            r = self._client.request(m, path, headers=headers)

        return self._parse_response(r)

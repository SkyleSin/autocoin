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

        # query_hash는 항상 urlencode 방식으로 계산
        hash_params = json_body or params
        auth = build_authorization_header(access, secret, params=hash_params)
        headers: dict[str, str] = {"Authorization": auth}

        if m == "GET":
            r = self._client.request(m, path, params=params, headers=headers)
        elif json_body is not None or params is not None:
            # POST: hash는 urlencode, 전송은 JSON body
            body_data = json_body or params
            headers["Content-Type"] = "application/json"
            r = self._client.request(m, path, headers=headers, json=body_data)
        else:
            r = self._client.request(m, path, headers=headers)

        return self._parse_response(r)

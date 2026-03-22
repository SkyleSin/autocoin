"""빗썸 REST 공통: HTTP 클라이언트·공개/Private 요청."""

from __future__ import annotations

import json
from typing import Any

import httpx

from bithumb.auth import build_authorization_header
from utils.config import Settings


class BithumbAPIError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Bithumb API error {status_code}: {body[:500]}")


class BithumbClientBase:
    """하위 `market` / `ticker` / `trades` 등에서 `public_request`·`private_request` 재사용."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._settings = settings or Settings()
        base = self._settings.bithumb_base_url.rstrip("/")
        self._client = httpx.Client(base_url=base, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BithumbClientBase:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _require_keys(self) -> tuple[str, str]:
        k, s = self._settings.bithumb_api_key, self._settings.bithumb_secret_key
        if not k or not s:
            msg = "Private API에는 BITHUMB_API_KEY, BITHUMB_SECRET_KEY가 필요합니다."
            raise ValueError(msg)
        return k, s

    def _parse_response(self, r: httpx.Response) -> Any:
        text = r.text
        if not r.is_success:
            raise BithumbAPIError(r.status_code, text)
        if not text.strip():
            return None
        return json.loads(text)

    def public_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        r = self._client.request(method.upper(), path, params=params)
        return self._parse_response(r)

    def private_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        access, secret = self._require_keys()
        m = method.upper()
        hash_params: dict[str, Any] | None = None
        if m == "GET" and params:
            hash_params = params
        elif m in {"POST", "PUT", "DELETE", "PATCH"} and json_body is not None:
            hash_params = json_body
        auth = build_authorization_header(access, secret, params=hash_params)
        headers = {"Authorization": auth}
        if m in {"POST", "PUT", "PATCH", "DELETE"} and json_body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            r = self._client.request(
                m,
                path,
                params=params,
                headers=headers,
                json=json_body,
            )
        else:
            r = self._client.request(m, path, params=params, headers=headers)
        return self._parse_response(r)

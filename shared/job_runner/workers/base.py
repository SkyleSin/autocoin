"""코인 거래소(현재 빗썸) REST 접근 공통.

WebSocket은 호스트·인증·수명주기가 REST와 달라 **별도 클라이언트/모듈**로 두는 전제를 둔다.
이 모듈은 동기 `httpx.Client` 기준; 나중에 `httpx.AsyncClient`나 `websockets`는
`job_runner.workers.ws`(예정) 같은 곳에서 연결한다.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from utils.config import Settings


class CoinServerAPIError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Coin server API error {status_code}: {body[:500]}")


class CoinServerClientBase:
    """REST 베이스: 베이스 URL·HTTP 클라이언트·응답 파싱.

    - `PublicRequester` / `PrivateRequester`가 상속해 엔드포인트 호출을 분리한다.
    - WebSocket은 이 클래스의 `_client`와 **공유하지 않는** 별 연결을 권장한다.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._settings = settings or Settings()
        base = self._settings.bithumb_base_url.rstrip("/")
        self._client = httpx.Client(base_url=base, timeout=timeout)

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def rest_base_url(self) -> str:
        return str(self._client.base_url).rstrip("/")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CoinServerClientBase:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _parse_response(self, response: httpx.Response) -> Any:
        text = response.text
        if not response.is_success:
            raise CoinServerAPIError(response.status_code, text)
        if not text.strip():
            return None
        return json.loads(text)

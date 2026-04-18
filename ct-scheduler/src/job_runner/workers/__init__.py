"""거래소(코인 서버) 연동: REST는 `PublicRequester` / `PrivateRequester`.

WebSocket은 REST `httpx.Client`와 수명·연결을 분리해 별 모듈에서 다루는 것을 권장한다.
"""

from .base import CoinServerAPIError, CoinServerClientBase
from .private_requester import PrivateRequester
from .public_requester import PublicRequester

__all__ = [
    "CoinServerAPIError",
    "CoinServerClientBase",
    "PrivateRequester",
    "PublicRequester",
]

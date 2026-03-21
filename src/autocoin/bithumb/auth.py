"""빗썸 Open API JWT 인증 (HS256 + 선택적 query_hash SHA512).

https://apidocs.bithumb.com/v2.1.5/docs/%EC%9D%B8%EC%A6%9D-%ED%97%A4%EB%8D%94-%EB%A7%8C%EB%93%A4%EA%B8%B0
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import jwt


def _encode_query_string(params: dict[str, Any]) -> bytes:
    """문서 예시와 같이 urlencoded 문자열의 UTF-8 바이트를 SHA512 입력으로 씁니다."""
    pairs: list[tuple[str, str]] = []
    for key in sorted(params.keys()):
        value = params[key]
        if isinstance(value, list | tuple):
            for item in value:
                pairs.append((f"{key}[]", str(item)))
        else:
            pairs.append((key, str(value)))
    return urlencode(pairs).encode("utf-8")


def build_authorization_header(
    access_key: str,
    secret_key: str,
    *,
    params: dict[str, Any] | None = None,
) -> str:
    """`Authorization: Bearer <jwt>` 값을 만듭니다."""
    payload: dict[str, Any] = {
        "access_key": access_key,
        "nonce": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
    }
    if params:
        raw = _encode_query_string(params)
        payload["query_hash"] = hashlib.sha512(raw).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    token = jwt.encode(payload, secret_key, algorithm="HS256")
    return f"Bearer {token}"

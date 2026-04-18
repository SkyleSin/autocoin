"""빗썸 Private API JWT 인증 헤더 (HS256 + query_hash)."""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import jwt


def _encode_query_string(params: dict[str, Any]) -> bytes:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
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
    payload: dict[str, Any] = {
        "access_key": access_key,
        "nonce": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
    }
    if params:
        # GET/POST 공통: urlencode → SHA512
        raw = _encode_query_string(params)
        payload["query_hash"] = hashlib.sha512(raw).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    token = jwt.encode(payload, secret_key, algorithm="HS256")
    return f"Bearer {token}"

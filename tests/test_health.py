"""통합 테스트: DATABASE_URL 이 있을 때만 실행 (Dev Container / CI)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL 없음 — Dev Container에서 실행하거나 .env 설정")
    from trader.api.app import app

    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_health_db(client: TestClient) -> None:
    r = client.get("/health/db")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "reachable"}

"""헬스 엔드포인트."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_health_db(client: TestClient) -> None:
    r = client.get("/health/db")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "reachable"}

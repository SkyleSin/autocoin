"""`/signals` API (DB + 선택적 Bearer)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _sample_signal() -> dict:
    return {
        "market": "KRW-BTC",
        "side": "hold",
        "source": "pytest",
        "reason": "api test",
        "payload": {"k": "v"},
    }


def test_create_and_list_signals(client: TestClient) -> None:
    body = _sample_signal()
    post = client.post("/signals", json=body)
    assert post.status_code == 200, post.text
    data = post.json()
    assert data["market"] == body["market"]
    assert data["side"] == body["side"]
    assert data["payload"] == body["payload"]

    listed = client.get("/signals", params={"limit": 5})
    assert listed.status_code == 200
    rows = listed.json()
    assert any(r["id"] == data["id"] for r in rows)


def test_signals_require_bearer_when_token_configured(app_with_api_token) -> None:
    with TestClient(app_with_api_token) as c:
        r = c.post("/signals", json=_sample_signal())
        assert r.status_code == 401

        r2 = c.get("/signals")
        assert r2.status_code == 401


def test_signals_with_valid_bearer(app_with_api_token) -> None:
    headers = {"Authorization": "Bearer pytest-bearer-token"}
    with TestClient(app_with_api_token) as c:
        r = c.post("/signals", json=_sample_signal(), headers=headers)
        assert r.status_code == 200, r.text

        r2 = c.get("/signals", headers=headers)
        assert r2.status_code == 200

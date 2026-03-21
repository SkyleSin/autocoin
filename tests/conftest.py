"""공통 fixture. DATABASE_URL 이 없으면 DB/API 통합 테스트는 스킵."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def require_database() -> None:
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL 없음 — Dev Container 또는 .env 설정")


@pytest.fixture
def app(require_database, monkeypatch: pytest.MonkeyPatch):
    """보호 API 테스트를 위해 기본은 토큰 비활성(.env 값도 env 변수로 덮어씀)."""
    monkeypatch.setenv("API_AUTH_TOKEN", "")
    from trader.api.app import create_app
    from trader.config import Settings

    return create_app(Settings())


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def app_with_api_token(require_database, monkeypatch: pytest.MonkeyPatch):
    """Authorization: Bearer 가 필요한 시나리오용."""
    monkeypatch.setenv("API_AUTH_TOKEN", "pytest-bearer-token")
    from trader.api.app import create_app
    from trader.config import Settings

    return create_app(Settings())

"""Report API 래퍼."""

from __future__ import annotations

from infra.api_client import api_post


async def generate_report(
    accounts: dict | None = None,
    ticker_prices: dict | None = None,
) -> dict:
    return await api_post(
        "/api/reports/generate",
        json={"accounts": accounts, "ticker_prices": ticker_prices},
    )


async def generate_detail_report() -> dict:
    return await api_post("/api/reports/generate-detail")


async def get_signal_summary(hours: int = 12) -> dict:
    return await api_post("/api/reports/signal-summary", json={"hours": hours})

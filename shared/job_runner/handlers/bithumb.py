"""빗썸 API 조회 핸들러 — markets, ticker, orders_chance."""

from __future__ import annotations

import asyncio
from typing import Any

from job_runner.exceptions import JobExecutionFailed
from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from job_runner.workers import PrivateRequester, PublicRequester
from models.job import Job
from models.job_status import JobStatus
from utils.config import Settings


def _markets_param_from_content(content: dict[str, Any] | None) -> str | None:
    if not content:
        return None
    m = content.get("market")
    if m is not None:
        s = str(m).strip()
        return s or None
    ms = content.get("markets")
    if ms is not None:
        parts = [p.strip() for p in str(ms).split(",") if p.strip()]
        return ",".join(parts) if parts else None
    return None


def _markets_sync(settings: Settings) -> list[dict[str, Any]]:
    with PublicRequester(settings) as pub:
        data = pub.request("GET", "/v1/market/all")
    if not isinstance(data, list):
        raise JobExecutionFailed("market/all 응답이 리스트가 아닙니다.")
    return data


def _ticker_sync(settings: Settings, markets: str) -> list[dict[str, Any]]:
    with PublicRequester(settings) as pub:
        data = pub.request("GET", "/v1/ticker", params={"markets": markets})
    if not isinstance(data, list):
        raise JobExecutionFailed("ticker 응답이 리스트가 아닙니다.")
    return data


def _orders_chance_sync(settings: Settings, market: str) -> dict[str, Any]:
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/orders/chance", params={"market": market})
    if not isinstance(data, dict):
        raise JobExecutionFailed("orders/chance 응답이 객체가 아닙니다.")
    return data


async def handle_bithumb_markets(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
) -> None:
    if rate_limiter is not None:
        await rate_limiter.acquire()
    data = await asyncio.to_thread(_markets_sync, settings)
    job.status = JobStatus.Complete.value
    job.response = {"markets": data}


async def handle_bithumb_ticker(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
) -> None:
    markets = _markets_param_from_content(job.content)
    if not markets:
        raise ValueError("bithumb_ticker 잡에 market 또는 markets가 없습니다.")
    if rate_limiter is not None:
        await rate_limiter.acquire()
    data = await asyncio.to_thread(_ticker_sync, settings, markets)
    job.status = JobStatus.Complete.value
    job.response = {"tickers": data}


async def handle_bithumb_orders_chance(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
) -> None:
    if not job.content or not str(job.content.get("market", "")).strip():
        raise ValueError("bithumb_orders_chance 잡에 content.market 이 필요합니다.")
    market = str(job.content["market"]).strip()
    if rate_limiter is not None:
        await rate_limiter.acquire()
    data = await asyncio.to_thread(_orders_chance_sync, settings, market)
    job.status = JobStatus.Complete.value
    job.response = {"chance": data}

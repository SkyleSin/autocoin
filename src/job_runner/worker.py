"""`PriorityQueue`에서 `(priority, job_id)`를 꺼내 DB 잡을 처리한다."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_engine.local_llm import query_ollama
from ai_engine.prompts import build_analysis_prompt
from analysis.chart import generate_chart_from_db_candles
from analysis.indicators import compute_all
from analysis.patterns import analyze as analyze_patterns
from analysis.signal_generator import evaluate
from analysis.support_resistance import analyze as analyze_sr
from job_runner.exceptions import JobExecutionFailed
from job_runner.poller import JobQueueItem, is_shutdown_queue_item
from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from job_runner.workers import CoinServerAPIError, PrivateRequester, PublicRequester
from models.candle import Candle
from models.job import Job
from models.job_status import JobStatus
from models.signal import Signal
from telegram.bot import send_signal_alert
from telegram.messages import format_combined_alert
from utils.config import Settings

logger = logging.getLogger(__name__)


def _should_retry_as_fail(exc: BaseException) -> bool:
    """네트워크·거래소 일시 오류 등 → `Failed`(재시도) 또는 `retry_max` 초과 시 `Error`."""
    if isinstance(
        exc,
        (
            JobExecutionFailed,
            CoinServerAPIError,
            httpx.RequestError,
            TimeoutError,
            ConnectionError,
            OSError,
        ),
    ):
        return True
    return False


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


def _candles_minutes_sync(
    settings: Settings, unit: int, market: str, count: int, to: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"market": market, "count": count}
    if to is not None:
        params["to"] = to
    with PublicRequester(settings) as pub:
        data = pub.request("GET", f"/v1/candles/minutes/{unit}", params=params)
    if not isinstance(data, list):
        raise JobExecutionFailed(f"candles/minutes/{unit} 응답이 리스트가 아닙니다.")
    return data


# 분 단위 → timeframe 문자열 매핑
_UNIT_TO_TIMEFRAME = {
    1: "1m", 3: "3m", 5: "5m", 10: "10m",
    15: "15m", 30: "30m", 60: "1h", 240: "4h",
}


async def _upsert_candles(
    session: AsyncSession, raw_candles: list[dict[str, Any]], timeframe: str,
) -> int:
    """빗썸 캔들 응답을 Candle 테이블에 upsert. 삽입/갱신된 행 수 반환."""
    count = 0
    for c in raw_candles:
        market = c["market"]
        open_at_str = c["candle_date_time_utc"]
        open_at = datetime.fromisoformat(open_at_str).replace(tzinfo=UTC)

        existing = (
            await session.execute(
                select(Candle).where(
                    Candle.market == market,
                    Candle.timeframe == timeframe,
                    Candle.open_at == open_at,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.open = Decimal(str(c["opening_price"]))
            existing.high = Decimal(str(c["high_price"]))
            existing.low = Decimal(str(c["low_price"]))
            existing.close = Decimal(str(c["trade_price"]))
            existing.volume = Decimal(str(c["candle_acc_trade_volume"]))
        else:
            session.add(Candle(
                market=market,
                timeframe=timeframe,
                open_at=open_at,
                open=Decimal(str(c["opening_price"])),
                high=Decimal(str(c["high_price"])),
                low=Decimal(str(c["low_price"])),
                close=Decimal(str(c["trade_price"])),
                volume=Decimal(str(c["candle_acc_trade_volume"])),
            ))
        count += 1
    return count


async def _run_technical_analysis(
    session: AsyncSession,
    market: str,
    timeframe: str,
    candle_limit: int = 200,
) -> dict[str, Any]:
    """DB에서 캔들 로드 → 지표 계산 → Signal 저장 → 결과 반환."""
    result_rows = await session.execute(
        select(Candle)
        .where(Candle.market == market, Candle.timeframe == timeframe)
        .order_by(Candle.open_at.asc())
        .limit(candle_limit)
    )
    candles = list(result_rows.scalars().all())

    if len(candles) < 26:
        return {
            "status": "insufficient_data",
            "candle_count": len(candles),
            "required": 26,
        }

    highs = pd.Series([float(c.high) for c in candles])
    lows = pd.Series([float(c.low) for c in candles])
    closes = pd.Series([float(c.close) for c in candles])

    snap = compute_all(closes)
    if snap is None:
        return {"status": "compute_failed", "candle_count": len(candles)}

    # 차트 패턴 + 지지/저항 분석
    pat = analyze_patterns(highs, lows, closes)
    sr = analyze_sr(highs, lows, closes)

    close_price = float(candles[-1].close)
    signal_result = evaluate(
        snap, close_price, pattern_analysis=pat, sr_analysis=sr,
    )

    # 분석 결과 통합 저장
    combined_indicators = {
        **snap.to_dict(),
        "patterns": pat.to_dict(),
        "support_resistance": sr.to_dict(),
    }

    signal = Signal(
        market=market,
        timeframe=timeframe,
        signal_type=signal_result.signal_type,
        confidence=signal_result.confidence,
        indicators=combined_indicators,
        source="technical",
    )
    session.add(signal)

    # BUY/SELL 시그널 → analyze_local_llm Job 자동 생성
    chain_created = False
    if signal_result.signal_type in ("BUY", "SELL"):
        llm_job = Job(
            type="analyze_local_llm",
            status=JobStatus.Ready.value,
            content={
                "market": market,
                "timeframe": timeframe,
                "close_price": close_price,
                "indicators": combined_indicators,
                "technical_signal": signal_result.signal_type,
                "technical_reasons": signal_result.reasons,
            },
            priority=15,
        )
        session.add(llm_job)
        chain_created = True

    return {
        "status": "ok",
        "candle_count": len(candles),
        "signal_type": signal_result.signal_type,
        "confidence": signal_result.confidence,
        "reasons": signal_result.reasons,
        "indicators": snap.to_dict(),
        "llm_chain": chain_created,
    }


def _orders_chance_sync(settings: Settings, market: str) -> dict[str, Any]:
    """GET /v1/orders/chance — 주문 가능 정보."""
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/orders/chance", params={"market": market})
    if not isinstance(data, dict):
        raise JobExecutionFailed("orders/chance 응답이 객체가 아닙니다.")
    return data


def _accounts_sync(settings: Settings) -> list[dict[str, Any]]:
    """GET /v1/accounts — 전체 잔고 조회."""
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/accounts")
    if not isinstance(data, list):
        raise JobExecutionFailed("accounts 응답이 리스트가 아닙니다.")
    return data


def _place_order_sync(
    settings: Settings,
    market: str,
    side: str,
    *,
    volume: str | None = None,
    price: str | None = None,
    ord_type: str = "limit",
) -> dict[str, Any]:
    """POST /v1/orders — 주문 실행."""
    body: dict[str, Any] = {
        "market": market,
        "side": side,
        "ord_type": ord_type,
    }
    if volume is not None:
        body["volume"] = volume
    if price is not None:
        body["price"] = price
    with PrivateRequester(settings) as prv:
        data = prv.request("POST", "/v1/orders", json_body=body)
    if not isinstance(data, dict):
        raise JobExecutionFailed("orders 응답이 객체가 아닙니다.")
    return data


def _get_order_sync(settings: Settings, uuid: str) -> dict[str, Any]:
    """GET /v1/order — 개별 주문 조회."""
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/order", params={"uuid": uuid})
    if not isinstance(data, dict):
        raise JobExecutionFailed("order 응답이 객체가 아닙니다.")
    return data


async def _execute_job(
    job: Job,
    settings: Settings,
    *,
    api_rate_limiter: AsyncSlidingWindowRateLimiter | None,
    session: AsyncSession | None = None,
) -> None:
    if job.type == "noop":
        job.status = JobStatus.Complete.value
        job.response = {"ok": True}
    elif job.type == "bithumb_markets":
        if api_rate_limiter is not None:
            await api_rate_limiter.acquire()
        data = await asyncio.to_thread(_markets_sync, settings)
        job.status = JobStatus.Complete.value
        job.response = {"markets": data}
    elif job.type == "bithumb_ticker":
        markets = _markets_param_from_content(job.content)
        if not markets:
            raise ValueError("bithumb_ticker 잡에 market 또는 markets가 없습니다.")
        if api_rate_limiter is not None:
            await api_rate_limiter.acquire()
        data = await asyncio.to_thread(_ticker_sync, settings, markets)
        job.status = JobStatus.Complete.value
        job.response = {"tickers": data}
    elif job.type == "bithumb_orders_chance":
        if not job.content or not str(job.content.get("market", "")).strip():
            raise ValueError("bithumb_orders_chance 잡에 content.market 이 필요합니다.")
        market = str(job.content["market"]).strip()
        data = await asyncio.to_thread(_orders_chance_sync, settings, market)
        job.status = JobStatus.Complete.value
        job.response = {"chance": data}
    elif job.type == "collect_candles":
        if not job.content or not str(job.content.get("market", "")).strip():
            raise ValueError("collect_candles 잡에 content.market 이 필요합니다.")
        market = str(job.content["market"]).strip()
        unit = int(job.content.get("unit", 1))
        count = int(job.content.get("count", 1))
        to = job.content.get("to")
        timeframe = _UNIT_TO_TIMEFRAME.get(unit, f"{unit}m")
        if api_rate_limiter is not None:
            await api_rate_limiter.acquire()
        raw = await asyncio.to_thread(_candles_minutes_sync, settings, unit, market, count, to)
        if session is not None:
            upserted = await _upsert_candles(session, raw, timeframe)
            job.response = {"upserted": upserted, "timeframe": timeframe}
            # 수집 완료 → analyze_technical Job 자동 생성
            analyze_job = Job(
                type="analyze_technical",
                status=JobStatus.Ready.value,
                content={"market": market, "timeframe": timeframe},
                priority=20,
            )
            session.add(analyze_job)
        else:
            job.response = {"candles": raw, "timeframe": timeframe}
        job.status = JobStatus.Complete.value
    elif job.type == "analyze_technical":
        if session is None:
            raise ValueError("analyze_technical 잡은 DB 세션이 필요합니다.")
        if not job.content or not str(job.content.get("market", "")).strip():
            raise ValueError("analyze_technical 잡에 content.market 이 필요합니다.")
        market = str(job.content["market"]).strip()
        timeframe = str(job.content.get("timeframe", "1m"))
        result = await _run_technical_analysis(session, market, timeframe)
        job.response = result
        job.status = JobStatus.Complete.value
    elif job.type == "analyze_local_llm":
        if session is None:
            raise ValueError("analyze_local_llm 잡은 DB 세션이 필요합니다.")
        if not job.content:
            raise ValueError("analyze_local_llm 잡에 content가 필요합니다.")
        market = str(job.content["market"]).strip()
        timeframe = str(job.content.get("timeframe", "1m"))
        close_price = float(job.content["close_price"])
        indicators = job.content["indicators"]
        tech_signal = str(job.content["technical_signal"])
        tech_reasons = list(job.content.get("technical_reasons", []))

        prompt = build_analysis_prompt(
            market, close_price, indicators, tech_signal, tech_reasons,
        )
        llm_result = await query_ollama(
            prompt,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
            num_gpu=settings.ollama_num_gpu,
        )

        signal = Signal(
            market=market,
            timeframe=timeframe,
            signal_type=llm_result.decision,
            confidence=llm_result.confidence,
            indicators=indicators,
            source="local_llm",
        )
        session.add(signal)

        job.response = {
            "decision": llm_result.decision,
            "confidence": llm_result.confidence,
            "reason": llm_result.reason,
            "raw_response": llm_result.raw_response[:1000],
        }
        job.status = JobStatus.Complete.value

        # 텔레그램 알림 전송 (차트 이미지 포함)
        if settings.telegram_bot_token and settings.telegram_chat_id:
            tech_conf = min(len(tech_reasons) * 0.25, 1.0)
            alert_text = format_combined_alert(
                market=market,
                close_price=close_price,
                tech_signal=tech_signal,
                tech_confidence=tech_conf,
                tech_reasons=tech_reasons,
                llm_decision=llm_result.decision,
                llm_confidence=llm_result.confidence,
                llm_reason=llm_result.reason,
                indicators=indicators,
            )
            # 차트 이미지 생성
            chart_image: bytes | None = None
            try:
                candle_rows = (await session.execute(
                    select(Candle)
                    .where(Candle.market == market, Candle.timeframe == timeframe)
                    .order_by(Candle.open_at.asc())
                    .limit(200)
                )).scalars().all()
                if len(candle_rows) >= 30:
                    chart_image = generate_chart_from_db_candles(
                        list(candle_rows),
                        market=market,
                        signal_type=llm_result.decision,
                        sr_analysis=indicators.get("support_resistance"),
                        pattern_analysis=indicators.get("patterns"),
                    )
            except Exception:
                logger.warning("차트 이미지 생성 실패, 텍스트만 전송", exc_info=True)
            await send_signal_alert(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                alert_text,
                chart_image=chart_image,
                market=market,
                signal_type=llm_result.decision,
                close_price=close_price,
            )
    elif job.type == "check_balance":
        if api_rate_limiter is not None:
            await api_rate_limiter.acquire()
        data = await asyncio.to_thread(_accounts_sync, settings)
        job.status = JobStatus.Complete.value
        job.response = {"accounts": data}
    elif job.type == "place_order":
        if not job.content or not str(job.content.get("market", "")).strip():
            raise ValueError("place_order 잡에 content.market 이 필요합니다.")
        market = str(job.content["market"]).strip()
        side = str(job.content.get("side", "")).strip()
        if side not in ("bid", "ask"):
            raise ValueError("place_order: side는 'bid'(매수) 또는 'ask'(매도)")
        volume = job.content.get("volume")
        price = job.content.get("price")
        ord_type = str(job.content.get("ord_type", "limit"))
        if api_rate_limiter is not None:
            await api_rate_limiter.acquire()
        data = await asyncio.to_thread(
            _place_order_sync, settings, market, side,
            volume=str(volume) if volume else None,
            price=str(price) if price else None,
            ord_type=ord_type,
        )
        job.status = JobStatus.Complete.value
        job.response = {"order": data}
    elif job.type == "check_order":
        if not job.content or not str(job.content.get("uuid", "")).strip():
            raise ValueError("check_order 잡에 content.uuid 가 필요합니다.")
        order_uuid = str(job.content["uuid"]).strip()
        if api_rate_limiter is not None:
            await api_rate_limiter.acquire()
        data = await asyncio.to_thread(_get_order_sync, settings, order_uuid)
        job.status = JobStatus.Complete.value
        job.response = {"order": data}
    else:
        raise ValueError(f"지원하지 않는 job type: {job.type}")


async def worker(
    queue: asyncio.PriorityQueue[JobQueueItem],
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    name: str,
    *,
    api_rate_limiter: AsyncSlidingWindowRateLimiter | None = None,
) -> None:
    try:
        while True:
            item: JobQueueItem = await queue.get()
            try:
                if is_shutdown_queue_item(item):
                    logger.info("%s: got shutdown sentinel, exit", name)
                    break
                _priority, job_id = item
                logger.info(
                    "%s: dequeued priority=%s job_id=%s qsize~=%s",
                    name,
                    _priority,
                    job_id,
                    queue.qsize(),
                )
                async with session_factory() as session:
                    job = await session.get(Job, job_id)
                    if job is None:
                        logger.warning("%s: job %s not found (skip)", name, job_id)
                    else:
                        if job.status != JobStatus.Queue.value:
                            logger.warning(
                                "%s: job_id=%s expected status Queue, got %s",
                                name,
                                job_id,
                                job.status,
                            )
                        job.status = JobStatus.Running.value
                        await session.commit()
                        logger.info(
                            "%s: execute start job_id=%s type=%s (status=Running)",
                            name,
                            job_id,
                            job.type,
                        )
                        try:
                            await _execute_job(
                                job,
                                settings,
                                api_rate_limiter=api_rate_limiter,
                                session=session,
                            )
                            await session.commit()
                            logger.info(
                                "%s: execute ok job_id=%s type=%s -> %s",
                                name,
                                job_id,
                                job.type,
                                job.status,
                            )
                        except Exception as e:
                            logger.exception("%s: job %s failed", name, job_id)
                            await session.rollback()
                            row = await session.get(Job, job_id)
                            if row is not None:
                                msg = str(e)[:2000]
                                if _should_retry_as_fail(e):
                                    next_cnt = row.retry_cnt + 1
                                    row.retry_cnt = next_cnt
                                    row.error_message = msg
                                    if next_cnt >= settings.job_retry_max:
                                        row.status = JobStatus.Error.value
                                    else:
                                        row.status = JobStatus.Failed.value
                                else:
                                    row.status = JobStatus.Error.value
                                    row.error_message = msg
                                await session.commit()
                                logger.info(
                                    "%s: execute fail job_id=%s -> %s retry_cnt=%s (%s)",
                                    name,
                                    job_id,
                                    row.status,
                                    row.retry_cnt,
                                    type(e).__name__,
                                )
            finally:
                queue.task_done()
    finally:
        logger.info("%s stopped", name)

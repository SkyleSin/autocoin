"""수집 스케줄러 — 캔들 수집 Job 발행 + 일별 분석 태스크."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

from infra.api.job import cleanup_jobs
from infra.api.report import generate_report
from infra.api.tracked_asset import get_active_markets
from infra.redis_queue import push_trade_job
from utils.config import Settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
REPORT_HOURS_KST = [9, 21]


# ---------------------------------------------------------------------------
# 다음 실행 시각 계산
# ---------------------------------------------------------------------------

def _next_kst_target(hour: int, minute: int) -> datetime:
    now_kst = datetime.now(KST)
    target = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_kst >= target:
        target += timedelta(days=1)
    return target.astimezone(UTC)


def _next_report_target() -> datetime:
    now_kst = datetime.now(KST)
    today = now_kst.replace(minute=0, second=0, microsecond=0)
    candidates = [today.replace(hour=h) for h in REPORT_HOURS_KST]
    tomorrow = today + timedelta(days=1)
    candidates += [tomorrow.replace(hour=h) for h in REPORT_HOURS_KST]
    future = [c for c in candidates if c > now_kst]
    return min(future).astimezone(UTC)


# ---------------------------------------------------------------------------
# 태스크별 실행 함수
# ---------------------------------------------------------------------------

async def _tick_candle(settings: Settings) -> None:
    """활성 마켓별 캔들 수집 Job → Redis queue (ct-worker 처리)."""
    assets = await get_active_markets()
    if not assets:
        logger.debug("collect/candle: 활성 마켓 없음")
        return

    markets = [a["market"] for a in assets]
    unit = settings.candle_unit
    count = settings.candle_count

    for market in markets:
        await push_trade_job({
            "type": "collect_candles",
            "payload": {"market": market, "unit": unit, "count": count},
        })

    # 참조 타임프레임 (추세 확인용)
    for tf_label in settings.candle_ref_timeframes.split(","):
        tf_label = tf_label.strip()
        if tf_label and tf_label != f"{unit}m":
            for market in markets:
                await push_trade_job({
                    "type": "collect_candles",
                    "payload": {"market": market, "timeframe": tf_label, "ref_only": True},
                })

    logger.info("collect/candle: %d markets → Redis queue", len(markets))


async def _tick_daily(settings: Settings) -> None:
    """일별 태스크 — 패턴 적중률 판정, 지표 가중치 갱신, Job 정리."""
    # 패턴 적중률 판정 (TODO: ct-scheduler 로컬 계산으로 구현)
    logger.info("collect/daily: pattern eval (TODO)")

    # 지표 가중치 갱신 (TODO: ct-scheduler 로컬 계산으로 구현)
    logger.info("collect/daily: weight update (TODO)")

    # Job 정리
    try:
        await cleanup_jobs(complete_days=3, error_days=7)
        logger.info("collect/daily: job cleanup 완료")
    except Exception:
        logger.exception("collect/daily: job cleanup 실패")


async def _tick_report(settings: Settings) -> None:
    """리포트 생성 — ct-api REST 호출."""
    try:
        await generate_report()
        logger.info("collect/report: 리포트 생성 완료")
    except Exception:
        logger.exception("collect/report: 리포트 생성 실패")


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------

async def collect_scheduler(settings: Settings) -> None:
    """수집 스케줄러 메인 루프."""
    logger.info("collect_scheduler: 시작")

    interval = settings.candle_interval_sec
    now = datetime.now(UTC)

    next_candle = now  # 즉시 첫 실행
    next_daily = _next_kst_target(0, 5)
    next_report = _next_report_target()

    try:
        while True:
            now = datetime.now(UTC)
            nearest = min(next_candle, next_daily, next_report)
            wait = (nearest - now).total_seconds()

            if wait > 0:
                await asyncio.sleep(wait)

            now = datetime.now(UTC)

            if now >= next_candle:
                try:
                    await _tick_candle(settings)
                except Exception:
                    logger.exception("collect/candle: 오류")
                next_candle = datetime.now(UTC) + timedelta(seconds=interval)

            if now >= next_daily:
                try:
                    await _tick_daily(settings)
                except Exception:
                    logger.exception("collect/daily: 오류")
                next_daily = _next_kst_target(0, 5)

            if now >= next_report:
                try:
                    await _tick_report(settings)
                except Exception:
                    logger.exception("collect/report: 오류")
                next_report = _next_report_target()

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("collect_scheduler: 종료")

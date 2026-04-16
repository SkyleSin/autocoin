"""FastAPI 앱 — DB 세션·`/jobs` CRUD·잡 poller(`PriorityQueue`).

잡 상태: Ready/Failed →(poller 클레임)→ Queue →(워커 실행 시작)→ Running → Complete/Failed/Error.
기동 시 Queue 행은 Ready로 되돌려 재클레임한다(프로세스 종료로 메모리 큐가 사라진 경우).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.collector.collect_scheduler import collect_scheduler
from job_runner.poller import SHUTDOWN_QUEUE_ITEM, JobQueueItem, poller
from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from job_runner.worker import worker as job_worker
from models.active_alert import ActiveAlert
from models.app_config import AppConfig
from models.candle import Candle
from models.indicator_snapshot import IndicatorSnapshot
from models.job import Job
from models.job_status import JobStatus
from models.pattern_accuracy import PatternAccuracy
from models.pattern_config import PatternConfig
from models.pattern_evaluation import PatternEvaluation
from models.shadow_trade import ShadowPortfolio, ShadowTrade
from models.signal import Signal
from models.todo import Todo
from models.tracked_asset import TrackedAsset
from models.trade import Trade
from routers.jobs import router as jobs_router
from services.config_service import load_risk_settings
from telegram.bot import send_message
from telegram.poller import telegram_poller
from utils.config import Settings
from utils.logging_setup import setup_logging

_logging_configured = False

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    global _logging_configured
    settings = settings or Settings()
    if not _logging_configured:
        setup_logging(settings=settings)
        _logging_configured = True
    if not settings.database_url.strip():
        msg = "DATABASE_URL이 필요합니다."
        raise ValueError(msg)

    engine = create_async_engine(settings.database_url.strip())
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            for table in (
                Job.__table__,
                Candle.__table__,
                TrackedAsset.__table__,
                Signal.__table__,
                Trade.__table__,
                AppConfig.__table__,
                ActiveAlert.__table__,
                PatternAccuracy.__table__,
                PatternConfig.__table__,
                PatternEvaluation.__table__,
                ShadowTrade.__table__,
                ShadowPortfolio.__table__,
                IndicatorSnapshot.__table__,
                Todo.__table__,
            ):
                await conn.run_sync(
                    lambda sync_conn, t=table: t.create(sync_conn, checkfirst=True),
                )
            # active_alerts에 acted_at 컬럼 추가 (기존 테이블 마이그레이션)
            await conn.execute(text("""
                ALTER TABLE active_alerts
                ADD COLUMN IF NOT EXISTS acted_at TIMESTAMPTZ DEFAULT NULL
            """))
            # signals에 version 컬럼 추가
            await conn.execute(text("""
                ALTER TABLE signals
                ADD COLUMN IF NOT EXISTS version VARCHAR(16) NOT NULL DEFAULT '1.0.1'
            """))
            # signals에 MFE/MAE 트래킹 컬럼 추가
            await conn.execute(text("""
                ALTER TABLE signals
                ADD COLUMN IF NOT EXISTS entry_price FLOAT DEFAULT NULL,
                ADD COLUMN IF NOT EXISTS exit_price FLOAT DEFAULT NULL,
                ADD COLUMN IF NOT EXISTS mfe FLOAT DEFAULT NULL,
                ADD COLUMN IF NOT EXISTS mae FLOAT DEFAULT NULL,
                ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ DEFAULT NULL,
                ADD COLUMN IF NOT EXISTS quality_score FLOAT DEFAULT NULL
            """))
            # 이전 프로세스에서 Queue 상태로 남은 잡 → Ready로 복구
            await conn.execute(
                text("UPDATE jobs SET status = :ready WHERE status = :queue"),
                {
                    "ready": JobStatus.Ready.value,
                    "queue": JobStatus.Queue.value,
                },
            )

        # DB에서 리스크 설정 로드
        async with session_factory() as session:
            changed = await load_risk_settings(session, settings)
            if changed:
                logger.info("DB에서 리스크 설정 로드: %s", changed)

        app.state.settings = settings
        app.state.session_factory = session_factory

        api_rate_limiter: AsyncSlidingWindowRateLimiter | None = None
        if settings.bithumb_max_requests_per_sec > 0:
            api_rate_limiter = AsyncSlidingWindowRateLimiter(
                settings.bithumb_max_requests_per_sec,
                window_seconds=1.0,
            )
        app.state.bithumb_api_rate_limiter = api_rate_limiter

        # strategy_queue: compute_indicators 완료 → 전략 실행
        from core.strategy.strategy_scheduler import StrategyEvent, strategy_scheduler
        from job_runner.handlers.indicators import set_strategy_queue

        strategy_queue: asyncio.Queue[StrategyEvent] = asyncio.Queue()
        set_strategy_queue(strategy_queue)

        strategy_task = asyncio.create_task(
            strategy_scheduler(strategy_queue, session_factory, settings),
            name="strategy-scheduler",
        )

        job_queue: asyncio.PriorityQueue[JobQueueItem] = asyncio.PriorityQueue()
        poller_task = asyncio.create_task(
            poller(job_queue, session_factory, settings),
            name="job-poller",
        )
        worker_tasks = [
            asyncio.create_task(
                job_worker(
                    job_queue,
                    session_factory,
                    settings,
                    f"job-worker-{i}",
                    api_rate_limiter=api_rate_limiter,
                ),
                name=f"job-worker-{i}",
            )
            for i in range(settings.job_worker_count)
        ]
        scheduler_task = asyncio.create_task(
            collect_scheduler(session_factory, settings),
            name="unified-scheduler",
        )
        telegram_poller_task = asyncio.create_task(
            telegram_poller(session_factory, settings),
            name="telegram-poller",
        )

        from core.safety.monitor import safety_monitor
        safety_monitor_task = asyncio.create_task(
            safety_monitor(session_factory, settings),
            name="safety-monitor",
        )

        from core.safety.shock_monitor import shock_monitor
        shock_monitor_task = asyncio.create_task(
            shock_monitor(session_factory, settings),
            name="shock-monitor",
        )

        app.state.job_queue = job_queue
        app.state.strategy_queue = strategy_queue
        app.state.job_poller_task = poller_task
        app.state.job_worker_tasks = worker_tasks
        app.state.scheduler_task = scheduler_task
        app.state.strategy_task = strategy_task
        app.state.telegram_poller_task = telegram_poller_task
        app.state.safety_monitor_task = safety_monitor_task
        logger.info(
            "job runner: poller=1 workers=%s bithumb_max_rps=%s "
            "candle_interval=%.0fs telegram=%s",
            settings.job_worker_count,
            settings.bithumb_max_requests_per_sec
            if settings.bithumb_max_requests_per_sec > 0
            else "off",
            settings.candle_interval_sec,
            "on" if settings.telegram_bot_token else "off",
        )

        # 서버 시작 텔레그램 알림
        if settings.telegram_bot_token and settings.telegram_chat_id:
            await _send_startup_alert(session_factory, settings)

        yield

        logger.info("lifespan: stop shock_monitor")
        shock_monitor_task.cancel()
        await asyncio.gather(shock_monitor_task, return_exceptions=True)

        logger.info("lifespan: stop safety_monitor")
        safety_monitor_task.cancel()
        await asyncio.gather(safety_monitor_task, return_exceptions=True)

        logger.info("lifespan: stop strategy_scheduler")
        strategy_task.cancel()
        await asyncio.gather(strategy_task, return_exceptions=True)

        logger.info("lifespan: stop collect_scheduler")
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)

        logger.info("lifespan: stop telegram_poller")
        telegram_poller_task.cancel()
        await asyncio.gather(telegram_poller_task, return_exceptions=True)

        logger.info("lifespan: stop poller")
        poller_task.cancel()
        await asyncio.gather(poller_task, return_exceptions=True)
        logger.info("lifespan: await job_queue.join() (drain work items)")
        await job_queue.join()
        logger.info(
            "lifespan: enqueue %s shutdown sentinel(s)",
            settings.job_worker_count,
        )
        for _ in range(settings.job_worker_count):
            await job_queue.put(SHUTDOWN_QUEUE_ITEM)
        await asyncio.gather(*worker_tasks)
        logger.info("app shutdown")
        await engine.dispose()

    app = FastAPI(title="autocoin", version="0.1.0", lifespan=lifespan)
    app.include_router(jobs_router)
    return app


settings = Settings()
app = create_app(settings)


def main() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )


async def _send_startup_alert(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """서버 시작 시 현재 상태를 텔레그램으로 전송."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    try:
        async with session_factory() as session:
            # 추적 자산
            assets = (await session.execute(
                select(TrackedAsset).where(TrackedAsset.is_active.is_(True))
            )).scalars().all()
            asset_list = ", ".join(f"`{a.market}`" for a in assets) if assets else "없음"

            # 최근 시그널
            last_signal = (await session.execute(
                select(Signal).order_by(Signal.created_at.desc()).limit(1)
            )).scalar_one_or_none()

        # LLM 모델 표시
        main_llm = (
            f"`{settings.deepseek_model}`"
            if settings.deepseek_api_key
            else f"`{settings.ollama_model}`"
        )
        sub_llm = (
            f"`{settings.ollama_model}`"
            if settings.deepseek_api_key
            else "없음"
        )

        msg = f"""🚀 *autocoin 서버 시작*

📋 *추적 자산:* {asset_list}
⏱️ *캔들 분석 주기:* {settings.candle_interval_sec:.0f}초
🛡️ *안전장치 감시:* {settings.safety_monitor_interval_sec:.0f}초 간격
🤖 *Main LLM:* {main_llm}
🔄 *Sub LLM (폴백):* {sub_llm}
👷 *워커:* {settings.job_worker_count}개

⚙️ *리스크 설정:*
• 1회 매매 상한: 잔고의 `{settings.risk_max_per_trade_pct}%`
• 일일 매매 횟수: `{settings.risk_max_daily_trades}회`
• 최소 유지 잔고: `{settings.risk_min_krw_reserve:,.0f}원`

🛡️ *안전장치:*
• 즉시 트리거: 지지선 `{settings.safety_breach_immediate_pct}%+` 이탈
• 연속 트리거: `{settings.safety_breach_consecutive_pct}%+` × `{settings.safety_breach_consecutive_count}분`"""

        if last_signal:
            msg += (
                f"\n\n📊 *최근 시그널:*\n"
                f"  {last_signal.market} → {last_signal.signal_type} "
                f"(conf={last_signal.confidence}, {last_signal.source})\n"
                f"  {last_signal.created_at:%Y-%m-%d %H:%M:%S}"
            )

        await send_message(token, chat_id, msg)
    except Exception:
        logger.warning("서버 시작 텔레그램 알림 실패", exc_info=True)


if __name__ == "__main__":
    main()

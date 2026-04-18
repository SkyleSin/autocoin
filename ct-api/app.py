"""FastAPI 앱 — DDL + Redis 초기화 + API 라우터.

백그라운드 태스크(수집/전략/안전장치)는 ct-scheduler, ct-strategy로 분리됨.
매매 Job 워커는 ct-worker가 담당.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infra.redis_pubsub import publish_telegram
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
from models.user import User
from routers.active_alerts import router as active_alerts_router
from routers.candles import router as candles_router
from routers.config import router as config_router
from routers.indicator_snapshots import router as snapshots_router
from routers.indicator_weights import router as indicator_weights_router
from routers.jobs import router as jobs_router
from routers.pattern_accuracy import router as pattern_accuracy_router
from routers.pattern_configs import router as pattern_configs_router
from routers.pattern_evaluations import router as pattern_evaluations_router
from routers.reports import router as reports_router
from routers.shadow import router as shadow_router
from routers.signals import router as signals_router
from routers.todos import router as todos_router
from routers.tracked_assets import router as tracked_assets_router
from routers.trades import router as trades_router
from routers.users import router as users_router
from services.config_service import load_risk_settings
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
                User.__table__,
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
            # signals/trades/active_alerts에 user_id 컬럼 추가
            await conn.execute(text("""
                ALTER TABLE signals
                ADD COLUMN IF NOT EXISTS user_id INT DEFAULT NULL
            """))
            await conn.execute(text("""
                ALTER TABLE trades
                ADD COLUMN IF NOT EXISTS user_id INT DEFAULT NULL
            """))
            await conn.execute(text("""
                ALTER TABLE active_alerts
                ADD COLUMN IF NOT EXISTS user_id INT DEFAULT NULL
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

        # Skyle 유저 시드
        await _seed_skyle_user(session_factory, settings)

        app.state.settings = settings
        app.state.session_factory = session_factory

        # Redis 초기화
        from infra.redis_client import init_redis

        await init_redis(settings.redis_url)

        logger.info("ct-api 시작 (DDL + API only)")

        # 서버 시작 텔레그램 알림
        if settings.telegram_bot_token and settings.telegram_chat_id:
            await _send_startup_alert(session_factory, settings)

        yield

        # Redis 종료
        from infra.redis_client import close_redis

        await close_redis()

        logger.info("ct-api shutdown")
        await engine.dispose()

    app = FastAPI(title="autocoin", version="0.1.0", lifespan=lifespan)
    app.include_router(jobs_router)
    app.include_router(tracked_assets_router)
    app.include_router(candles_router)
    app.include_router(signals_router)
    app.include_router(snapshots_router)
    app.include_router(trades_router)
    app.include_router(users_router)
    app.include_router(todos_router)
    app.include_router(config_router)
    app.include_router(active_alerts_router)
    app.include_router(indicator_weights_router)
    app.include_router(pattern_accuracy_router)
    app.include_router(pattern_configs_router)
    app.include_router(pattern_evaluations_router)
    app.include_router(reports_router)
    app.include_router(shadow_router)
    return app


settings = Settings()
app = create_app(settings)


def main() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )


async def _seed_skyle_user(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Skyle 유저가 없으면 생성."""
    if not settings.encrypt_key or not settings.telegram_chat_id:
        logger.warning("ENCRYPT_KEY 또는 TELEGRAM_CHAT_ID 미설정 — Skyle 유저 시드 스킵")
        return
    async with session_factory() as session:
        existing = await session.execute(
            select(User).where(User.username == "Skyle")
        )
        if existing.scalar_one_or_none() is not None:
            return
        from utils.crypto import encrypt

        user = User(
            telegram_id=int(settings.telegram_chat_id),
            username="Skyle",
            bithumb_api_key=encrypt(settings.bithumb_api_key, settings.encrypt_key),
            bithumb_secret_key=encrypt(
                settings.bithumb_secret_key, settings.encrypt_key,
            ),
            strategy_type="topdown_mtf",
            coin_watchlist=[],
            risk_max_per_trade_pct=settings.risk_max_per_trade_pct,
            risk_max_daily_trades=settings.risk_max_daily_trades,
            risk_min_krw_reserve=settings.risk_min_krw_reserve,
        )
        session.add(user)
        await session.commit()
        logger.info("Skyle 유저 생성 완료")


async def _send_startup_alert(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """서버 시작 시 현재 상태를 텔레그램으로 전송."""
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
• 연속 트리거: `{settings.safety_breach_consecutive_pct}%+`\
 × `{settings.safety_breach_consecutive_count}분`"""

        if last_signal:
            msg += (
                f"\n\n📊 *최근 시그널:*\n"
                f"  {last_signal.market} → {last_signal.signal_type} "
                f"(conf={last_signal.confidence}, {last_signal.source})\n"
                f"  {last_signal.created_at:%Y-%m-%d %H:%M:%S}"
            )

        await publish_telegram(chat_id, msg)
    except Exception:
        logger.warning("서버 시작 텔레그램 알림 실패", exc_info=True)


if __name__ == "__main__":
    main()

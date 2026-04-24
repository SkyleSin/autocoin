"""FastAPI 앱 — DDL + Redis 초기화 + API 라우터.

백그라운드 태스크(수집/전략/안전장치)는 ct-scheduler, ct-strategy로 분리됨.
매매 Job 워커는 ct-worker가 담당.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from models.active_alert import ActiveAlert
from models.app_config import AppConfig
from models.candle import Candle
from models.indicator_snapshot import IndicatorSnapshot
from models.market import Market
from models.pattern_accuracy import PatternAccuracy
from models.pattern_config import PatternConfig
from models.pattern_evaluation import PatternEvaluation
from models.shadow_trade import ShadowPortfolio, ShadowTrade
from models.signal import Signal
from models.tracked_asset import TrackedAsset
from models.trade import Trade
from models.user import User
from models.user_portfolio import UserPortfolio
from routers.active_alerts import router as active_alerts_router
from routers.candles import router as candles_router
from routers.config import router as config_router
from routers.indicator_snapshots import router as snapshots_router
from routers.indicator_weights import router as indicator_weights_router
from routers.markets import router as markets_router
from routers.pattern_accuracy import router as pattern_accuracy_router
from routers.pattern_configs import router as pattern_configs_router
from routers.pattern_evaluations import router as pattern_evaluations_router
from routers.portfolios import router as portfolios_router
from routers.reports import router as reports_router
from routers.shadow import router as shadow_router
from routers.signals import router as signals_router
from routers.tracked_assets import router as tracked_assets_router
from routers.trades import router as trades_router
from routers.users import router as users_router
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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
                UserPortfolio.__table__,
                Market.__table__,
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
            # users에서 strategy_type, strategy_params 컬럼 제거
            await conn.execute(text("""
                ALTER TABLE users
                DROP COLUMN IF EXISTS strategy_type,
                DROP COLUMN IF EXISTS strategy_params
            """))
            # users에 risk_settings JSONB 추가
            await conn.execute(text("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS risk_settings JSONB
                DEFAULT '{"max_per_trade_pct": 15.0, "max_daily_trades": 30, "min_krw_reserve": 100000}'::jsonb
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
            # active_alerts: user_id NOT NULL + 유저별 유니크 인덱스로 전환
            await conn.execute(text("""
                DELETE FROM active_alerts WHERE user_id IS NULL
            """))
            await conn.execute(text("""
                ALTER TABLE active_alerts
                ALTER COLUMN user_id SET NOT NULL
            """))
            await conn.execute(text("""
                DROP INDEX IF EXISTS uq_active_alerts_market_tf
            """))
            await conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_alerts_user_market_tf
                ON active_alerts (user_id, market, timeframe)
            """))
            # users에 is_shadow 컬럼 추가
            await conn.execute(text("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS is_shadow BOOLEAN DEFAULT false
            """))
            # tracked_assets에 현재가 컬럼 추가
            await conn.execute(text("""
                ALTER TABLE tracked_assets
                ADD COLUMN IF NOT EXISTS current_price NUMERIC(24,4) DEFAULT NULL,
                ADD COLUMN IF NOT EXISTS price_updated_at TIMESTAMPTZ DEFAULT NULL
            """))
        # 유저 시드
        await _seed_users(session_factory, settings)

        app.state.settings = settings
        app.state.session_factory = session_factory

        # Redis 초기화
        from infra.redis_client import init_redis

        await init_redis(settings.redis_url)

        logger.info("ct-api 시작 (DDL + API only)")

        # 서버 시작 텔레그램 알림 (master 봇)
        if settings.master_bot_token and settings.master_telegram_id:
            await _send_startup_alert(session_factory, settings)

        yield

        # Redis 종료
        from infra.redis_client import close_redis

        await close_redis()

        logger.info("ct-api shutdown")
        await engine.dispose()

    app = FastAPI(title="autocoin", version="0.1.0", lifespan=lifespan)
    app.include_router(tracked_assets_router)
    app.include_router(candles_router)
    app.include_router(signals_router)
    app.include_router(snapshots_router)
    app.include_router(trades_router)
    app.include_router(users_router)
    app.include_router(config_router)
    app.include_router(active_alerts_router)
    app.include_router(indicator_weights_router)
    app.include_router(pattern_accuracy_router)
    app.include_router(pattern_configs_router)
    app.include_router(markets_router)
    app.include_router(pattern_evaluations_router)
    app.include_router(portfolios_router)
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


async def _seed_users(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Skyle 유저 시드."""
    async with session_factory() as session:
        # ── Skyle ──
        if not settings.encrypt_key:
            logger.warning("ENCRYPT_KEY 또는 TELEGRAM_CHAT_ID 미설정 — Skyle 유저 시드 스킵")
            return

        existing_skyle = (await session.execute(
            select(User).where(User.username == "Skyle")
        )).scalar_one_or_none()
        if existing_skyle is None:
            from utils.crypto import encrypt

            session.add(User(
                telegram_id=int(settings.telegram_chat_id),
                username="Skyle",
                bithumb_api_key=encrypt(settings.bithumb_api_key, settings.encrypt_key),
                bithumb_secret_key=encrypt(
                    settings.bithumb_secret_key, settings.encrypt_key,
                ),
                coin_watchlist=[],
            ))
            await session.commit()
            logger.info("Skyle 유저 생성 완료")


async def _send_startup_alert(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """서버 시작 시 현재 상태를 master 봇으로 전송."""
    from infra.redis_pubsub import publish_master

    chat_id = settings.master_telegram_id

    try:
        async with session_factory() as session:
            assets = (await session.execute(
                select(TrackedAsset).where(TrackedAsset.is_active.is_(True))
            )).scalars().all()
            asset_list = ", ".join(f"`{a.market}`" for a in assets) if assets else "없음"

            last_signal = (await session.execute(
                select(Signal).order_by(Signal.created_at.desc()).limit(1)
            )).scalar_one_or_none()

        msg = f"""🚀 *autocoin v2 서버 시작*

📋 *추적 자산:* {asset_list}
🛡️ *안전장치 감시:* {settings.safety_monitor_interval_sec:.0f}초 간격"""

        if last_signal:
            msg += (
                f"\n\n📊 *최근 시그널:*\n"
                f"  {last_signal.market} → {last_signal.signal_type} "
                f"(conf={last_signal.confidence}, {last_signal.source})\n"
                f"  {last_signal.created_at:%Y-%m-%d %H:%M:%S}"
            )

        await publish_master(chat_id, msg)
    except Exception:
        logger.warning("서버 시작 텔레그램 알림 실패", exc_info=True)


if __name__ == "__main__":
    main()

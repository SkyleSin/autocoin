"""캔들 수집 파이프라인 E2E 테스트.

1. DB 테이블 생성
2. TrackedAsset에 KRW-BTC 등록
3. collect_candles Job 생성 → 실행
4. Candle 테이블에 데이터 적재 확인
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# src를 PYTHONPATH에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.base import Base
from models.candle import Candle
from models.job import Job
from models.job_status import JobStatus
from models.tracked_asset import TrackedAsset
from utils.config import Settings


async def main() -> None:
    settings = Settings()
    if not settings.database_url:
        print("ERROR: DATABASE_URL이 설정되지 않았습니다.")
        return

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 1) 테이블 생성
    print("=== 1. 테이블 생성 ===")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("  OK: 모든 테이블 생성 완료")

    # 2) TrackedAsset 등록 (KRW-BTC)
    print("\n=== 2. TrackedAsset 등록 ===")
    async with session_factory() as session:
        existing = (
            await session.execute(
                select(TrackedAsset).where(
                    TrackedAsset.market == "KRW-BTC",
                    TrackedAsset.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

        if existing:
            print(f"  이미 존재: id={existing.id} market={existing.market}")
        else:
            asset = TrackedAsset(market="KRW-BTC", intent="watch", is_active=True)
            session.add(asset)
            await session.commit()
            await session.refresh(asset)
            print(f"  생성 완료: id={asset.id} market={asset.market}")

    # 3) 빗썸 캔들 API 직접 호출 테스트
    print("\n=== 3. 빗썸 캔들 API 호출 테스트 ===")
    from job_runner.workers import PublicRequester

    try:
        raw_candles = await asyncio.to_thread(_fetch_candles, settings)
        print(f"  OK: {len(raw_candles)}개 캔들 수신")
        if raw_candles:
            c = raw_candles[0]
            print(f"  샘플: market={c['market']} time={c['candle_date_time_utc']}")
            print(f"         open={c['opening_price']} close={c['trade_price']}")
            print(f"         high={c['high_price']} low={c['low_price']}")
            print(f"         volume={c['candle_acc_trade_volume']}")
    except Exception as e:
        print(f"  FAIL: {e}")
        await engine.dispose()
        return

    # 4) Candle upsert 테스트
    print("\n=== 4. Candle DB upsert 테스트 ===")
    from job_runner.handlers.candle import _upsert_candles

    async with session_factory() as session:
        count = await _upsert_candles(session, raw_candles, "1m")
        await session.commit()
        print(f"  OK: {count}개 upsert 완료")

    # 5) DB에서 확인
    print("\n=== 5. Candle 테이블 조회 ===")
    async with session_factory() as session:
        candles = (
            await session.execute(
                select(Candle)
                .where(Candle.market == "KRW-BTC")
                .order_by(Candle.open_at.desc())
                .limit(5)
            )
        ).scalars().all()

        if not candles:
            print("  WARNING: 캔들 데이터 없음")
        else:
            for c in candles:
                print(
                    f"  {c.market} {c.timeframe} {c.open_at} "
                    f"O={c.open} H={c.high} L={c.low} C={c.close} V={c.volume}"
                )

    # 6) 중복 upsert 테스트 (같은 데이터 다시 넣기)
    print("\n=== 6. 중복 upsert 테스트 (업데이트 확인) ===")
    async with session_factory() as session:
        count = await _upsert_candles(session, raw_candles, "1m")
        await session.commit()
        print(f"  OK: {count}개 재upsert (기존 행 업데이트)")

    total = await _count_candles(session_factory)
    print(f"  총 캔들 수: {total} (중복 없이 유지되어야 함)")

    print("\n=== 테스트 완료 ===")
    await engine.dispose()


def _fetch_candles(settings: Settings) -> list[dict]:
    from job_runner.workers import PublicRequester

    with PublicRequester(settings) as pub:
        data = pub.request("GET", "/v1/candles/minutes/1", params={"market": "KRW-BTC", "count": 3})
    if not isinstance(data, list):
        raise TypeError("캔들 응답이 리스트가 아닙니다.")
    return data


async def _count_candles(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.execute(text("SELECT count(*) FROM candles"))
        return result.scalar_one()


if __name__ == "__main__":
    asyncio.run(main())

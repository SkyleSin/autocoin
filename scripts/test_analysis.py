"""기술 지표 분석 E2E 테스트.

1. 빗썸에서 KRW-BTC 1분봉 200개 수집 → DB 적��
2. 기술 지표 계산 + 시그널 생성
3. Signal 테이블 확인
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.base import Base
from models.candle import Candle
from models.signal import Signal
from utils.config import Settings


async def main() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 테이블 생성
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 1) 캔들 200개 수집
    print("=== 1. KRW-BTC 1분봉 200개 수집 ===")
    from job_runner.worker import _upsert_candles

    def _fetch_200(settings: Settings) -> list[dict]:
        from job_runner.workers import PublicRequester
        with PublicRequester(settings) as pub:
            return pub.request(
                "GET", "/v1/candles/minutes/1",
                params={"market": "KRW-BTC", "count": 200},
            )

    raw = await asyncio.to_thread(_fetch_200, settings)
    print(f"  수신: {len(raw)}개")

    async with session_factory() as session:
        cnt = await _upsert_candles(session, raw, "1m")
        await session.commit()
    print(f"  DB upsert: {cnt}개")

    # 2) DB 캔들 수 확인
    async with session_factory() as session:
        total = (await session.execute(
            text("SELECT count(*) FROM candles WHERE market='KRW-BTC' AND timeframe='1m'")
        )).scalar_one()
    print(f"  DB 총 캔들: {total}개")

    # 3) 기술 지표 분석 실행
    print("\n=== 2. 기술 지표 분석 ===")
    from job_runner.worker import _run_technical_analysis

    async with session_factory() as session:
        result = await _run_technical_analysis(session, "KRW-BTC", "1m")
        await session.commit()

    print(f"  상태: {result['status']}")
    if result["status"] == "ok":
        print(f"  캔들 수: {result['candle_count']}")
        print(f"  시그널: {result['signal_type']} (confidence={result['confidence']})")
        print(f"  판단 근거:")
        for r in result["reasons"]:
            print(f"    - {r}")
        print(f"  지표:")
        for k, v in result["indicators"].items():
            if v is not None:
                print(f"    {k}: {v}")
    else:
        print(f"  결과: {result}")

    # 4) Signal 테이블 확인
    print("\n=== 3. Signal 테이블 확인 ===")
    async with session_factory() as session:
        signals = (
            await session.execute(
                select(Signal)
                .where(Signal.market == "KRW-BTC")
                .order_by(Signal.created_at.desc())
                .limit(3)
            )
        ).scalars().all()

        if not signals:
            print("  WARNING: 시그널 없음")
        else:
            for s in signals:
                print(
                    f"  [{s.source}] {s.market} {s.timeframe} → {s.signal_type} "
                    f"(confidence={s.confidence}) at {s.created_at}"
                )

    print("\n=== 테스트 완료 ===")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

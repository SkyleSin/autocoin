"""3단계 파이프라인 E2E 테스트: 캔들 수집 → 기술분석 → 로컬 LLM 판단."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.analysis.indicators import compute_all
from core.analysis.signal_generator import evaluate
from core.strategy.llm.local_llm import query_ollama
from core.strategy.llm.prompts import build_analysis_prompt
from job_runner.handlers.candle import _upsert_candles
from models.base import Base
from models.candle import Candle
from models.signal import Signal
from utils.config import Settings


async def main() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 1) 캔들 200개 수집
    print("=== 1. 캔들 수집 ===")

    def _fetch(s: Settings) -> list[dict]:
        from job_runner.workers import PublicRequester
        with PublicRequester(s) as pub:
            return pub.request(
                "GET", "/v1/candles/minutes/1",
                params={"market": "KRW-BTC", "count": 200},
            )

    raw = await asyncio.to_thread(_fetch, settings)
    async with sf() as session:
        cnt = await _upsert_candles(session, raw, "1m")
        await session.commit()
    print(f"  {cnt}개 캔들 upsert 완료")

    # 2) 기술 지표 분석
    print("\n=== 2. 기술 지표 분석 ===")
    import pandas as pd

    async with sf() as session:
        rows = (await session.execute(
            select(Candle)
            .where(Candle.market == "KRW-BTC", Candle.timeframe == "1m")
            .order_by(Candle.open_at.asc())
            .limit(200)
        )).scalars().all()

    closes = pd.Series([float(c.close) for c in rows])
    snap = compute_all(closes)
    close_price = float(rows[-1].close)
    signal_result = evaluate(snap, close_price)

    print(f"  시그널: {signal_result.signal_type} (confidence={signal_result.confidence})")
    print(f"  근거: {signal_result.reasons}")

    # 3) 로컬 LLM 판단 (시그널과 무관하게 테스트용으로 호출)
    print("\n=== 3. 로컬 LLM 판단 (gemma3:4b) ===")
    prompt = build_analysis_prompt(
        "KRW-BTC",
        close_price,
        snap.to_dict(),
        signal_result.signal_type,
        signal_result.reasons,
    )

    llm_result = await query_ollama(
        prompt,
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout=settings.ollama_timeout,
        num_gpu=settings.ollama_num_gpu,
    )

    print(f"  LLM 판단: {llm_result.decision} (confidence={llm_result.confidence})")
    print(f"  이유: {llm_result.reason}")

    # Signal 저장
    async with sf() as session:
        session.add(Signal(
            market="KRW-BTC",
            timeframe="1m",
            signal_type=llm_result.decision,
            confidence=llm_result.confidence,
            indicators=snap.to_dict(),
            source="local_llm",
        ))
        await session.commit()

    # 4) 전체 시그널 이력 확인
    print("\n=== 4. 시그널 이력 ===")
    async with sf() as session:
        signals = (await session.execute(
            select(Signal)
            .where(Signal.market == "KRW-BTC")
            .order_by(Signal.created_at.desc())
            .limit(5)
        )).scalars().all()
        for s in signals:
            print(f"  [{s.source:12s}] {s.signal_type:4s} conf={s.confidence} {s.created_at}")

    print("\n=== 파이프라인 테스트 완료 ===")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

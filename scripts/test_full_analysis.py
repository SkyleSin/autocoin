"""통합 분석 E2E 테스트: 기술지표 + 차트패턴 + 지지/저항 + LLM."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.analysis.indicators import compute_all
from core.analysis.patterns import analyze as analyze_patterns
from core.analysis.signal_generator import evaluate
from core.analysis.support_resistance import analyze as analyze_sr
from job_runner.handlers.candle import _upsert_candles
from models.base import Base
from models.candle import Candle
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
        await _upsert_candles(session, raw, "1m")
        await session.commit()
    print(f"  {len(raw)}개 캔들 upsert")

    # 2) DB에서 캔들 로드
    async with sf() as session:
        rows = (await session.execute(
            select(Candle)
            .where(Candle.market == "KRW-BTC", Candle.timeframe == "1m")
            .order_by(Candle.open_at.asc())
            .limit(200)
        )).scalars().all()

    highs = pd.Series([float(c.high) for c in rows])
    lows = pd.Series([float(c.low) for c in rows])
    closes = pd.Series([float(c.close) for c in rows])
    close_price = float(rows[-1].close)

    # 3) 기술 지표
    print("\n=== 2. 기술 지표 ===")
    snap = compute_all(closes)
    for k, v in snap.to_dict().items():
        if v is not None:
            print(f"  {k}: {v}")

    # 4) 차트 패턴
    print("\n=== 3. 차트 패턴 ===")
    pat = analyze_patterns(highs, lows, closes)
    if pat.patterns:
        for p in pat.patterns:
            print(f"  [{p.signal}] {p.name_ko} (신뢰도 {p.confidence}%) — {p.description}")
    else:
        print("  탐지된 패턴 없음")

    # 5) 지지/저항
    print("\n=== 4. 지지/저항선 ===")
    sr = analyze_sr(highs, lows, closes)
    if sr.levels:
        for lv in sr.levels[:5]:
            print(f"  [{lv.level_type:10s}] {lv.price:>15,.0f} (터치 {lv.touch_count}회, 강도 {lv.strength})")
    else:
        print("  탐지된 지지/저항선 없음")
    if sr.nearest_support:
        print(f"  가장 가까운 지지선: {sr.nearest_support:,.0f} (거리 {sr.distance_to_support_pct}%)")
    if sr.nearest_resistance:
        print(f"  가장 가까운 저항선: {sr.nearest_resistance:,.0f} (거리 {sr.distance_to_resistance_pct}%)")
    print(f"  지지/저항 시그널: {sr.signal}")

    # 6) 종합 판단
    print("\n=== 5. 종합 시그널 ===")
    result = evaluate(snap, close_price, pattern_analysis=pat, sr_analysis=sr)
    print(f"  시그널: {result.signal_type} (confidence={result.confidence})")
    print("  판단 근거:")
    for r in result.reasons:
        print(f"    - {r}")

    print(f"\n  현재가: {close_price:,.0f} KRW")

    print("\n=== 테스트 완료 ===")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

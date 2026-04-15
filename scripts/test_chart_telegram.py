"""차트 이미지 + 텔레그램 전송 E2E 테스트."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.analysis.chart import generate_chart_from_db_candles
from core.analysis.indicators import compute_all
from core.analysis.patterns import analyze as analyze_patterns
from core.analysis.signal_generator import evaluate
from core.analysis.support_resistance import analyze as analyze_sr
from models.base import Base
from models.candle import Candle
from telegram.bot import send_signal_alert
from telegram.messages import format_combined_alert
from utils.config import Settings


async def main() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 1) 캔들 로드
    print("=== 1. 캔들 로드 ===")
    async with sf() as session:
        rows = (await session.execute(
            select(Candle)
            .where(Candle.market == "KRW-BTC", Candle.timeframe == "1m")
            .order_by(Candle.open_at.asc())
            .limit(200)
        )).scalars().all()
    print(f"  {len(rows)}개 캔들")

    # 2) 분석 실행
    print("\n=== 2. 분석 ===")
    highs = pd.Series([float(c.high) for c in rows])
    lows = pd.Series([float(c.low) for c in rows])
    closes = pd.Series([float(c.close) for c in rows])

    snap = compute_all(closes)
    pat = analyze_patterns(highs, lows, closes)
    sr = analyze_sr(highs, lows, closes)
    signal = evaluate(snap, float(rows[-1].close), pattern_analysis=pat, sr_analysis=sr)

    print(f"  시그널: {signal.signal_type} ({signal.confidence})")
    print(f"  패턴: {[p.name_ko for p in pat.patterns]}")
    print(f"  지지/저항: S={sr.nearest_support} R={sr.nearest_resistance}")

    # 3) 차트 이미지 생성
    print("\n=== 3. 차트 이미지 생성 ===")
    chart_bytes = generate_chart_from_db_candles(
        list(rows),
        market="KRW-BTC",
        signal_type=signal.signal_type,
        sr_analysis=sr.to_dict(),
        pattern_analysis=pat.to_dict(),
    )
    print(f"  이미지 크기: {len(chart_bytes):,} bytes")

    # 4) 텔레그램 전송
    print("\n=== 4. 텔레그램 전송 ===")
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("  SKIP: 텔레그램 설정 없음")
    else:
        alert_text = format_combined_alert(
            market="KRW-BTC",
            close_price=float(rows[-1].close),
            tech_signal=signal.signal_type,
            tech_confidence=signal.confidence,
            tech_reasons=signal.reasons,
            llm_decision=signal.signal_type,
            llm_confidence=signal.confidence,
            llm_reason="테스트 — 차트 이미지 전송 확인",
            indicators={
                "patterns": pat.to_dict(),
                "support_resistance": sr.to_dict(),
            },
        )
        ok = await send_signal_alert(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            alert_text,
            chart_image=chart_bytes,
        )
        print(f"  결과: {'성공' if ok else '실패'}")

    print("\n=== 테스트 완료 — 텔레그램 확인해주세요 ===")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

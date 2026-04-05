"""매매 인프라 E2E 테스트 (실제 주문 X, 잔고 조회 + 리스크 검증만)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from executor.risk_manager import check_risk
from executor.trade_executor import execute_trade
from models.base import Base
from utils.config import Settings


async def main() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 1) 잔고 조회
    print("=== 1. 빗썸 잔고 조회 ===")
    try:
        from job_runner.handlers.trade import _accounts_sync
        accounts = await asyncio.to_thread(_accounts_sync, settings)
        for acc in accounts:
            currency = acc.get("currency", "?")
            balance = acc.get("balance", "0")
            locked = acc.get("locked", "0")
            print(f"  {currency}: 잔고={balance}, 묶임={locked}")

        # KRW 잔고 추출
        krw_acc = next((a for a in accounts if a.get("currency") == "KRW"), None)
        krw_balance = float(krw_acc["balance"]) if krw_acc else 0
        print(f"\n  KRW 가용 잔고: {krw_balance:,.0f}")
    except Exception as e:
        print(f"  잔고 조회 실패 (API 키 미설정?): {e}")
        krw_balance = 1_000_000  # 테스트용 가상 잔고
        print(f"  테스트용 가상 잔고 사용: {krw_balance:,.0f}")

    # 2) 리스크 검증 테스트
    print("\n=== 2. 리스크 검증 ===")
    async with sf() as session:
        # 정상 케이스
        risk = await check_risk(
            session, settings,
            side="bid", market="KRW-BTC",
            krw_balance=krw_balance,
            intended_amount_krw=krw_balance * 0.03,
        )
        print(f"  정상 매수 (3%): allowed={risk.allowed}, "
              f"max={risk.max_amount_krw:,.0f}, reason={risk.reason}")

        # 초과 케이스
        risk2 = await check_risk(
            session, settings,
            side="bid", market="KRW-BTC",
            krw_balance=krw_balance,
            intended_amount_krw=krw_balance * 0.99,
        )
        print(f"  과다 매수 (99%): allowed={risk2.allowed}, reason={risk2.reason}")

        # 잔고 부족
        risk3 = await check_risk(
            session, settings,
            side="bid", market="KRW-BTC",
            krw_balance=50_000,
            intended_amount_krw=10_000,
        )
        print(f"  잔고 부족 (5만원): allowed={risk3.allowed}, reason={risk3.reason}")

    # 3) 매매 실행 시뮬레이션 (주문은 안 함)
    print("\n=== 3. 매매 실행 시뮬레이션 ===")
    async with sf() as session:
        result = await execute_trade(
            session, settings,
            market="KRW-BTC",
            side="BUY",
            close_price=101_800_000,
            signal_type="BUY",
            confidence=0.85,
            krw_balance=krw_balance,
        )
        await session.commit()
        print(f"  상태: {result['status']}")
        if result["status"] == "approved":
            print(f"  trade_id: {result['trade_id']}")
            print(f"  주문 내용: {result['order_content']}")
            print(f"  투자금: {result['intended_amount_krw']:,.0f} KRW")
        else:
            print(f"  거부 사유: {result['reason']}")

    print("\n=== 테스트 완료 ===")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

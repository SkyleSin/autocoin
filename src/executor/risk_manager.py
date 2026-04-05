"""리스크 관리 — 매매 전 안전 검증."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.trade import Trade
from utils.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskCheck:
    """리스크 검증 결과."""

    allowed: bool
    reason: str
    max_amount_krw: float  # 이번 매매에 사용 가능한 최대 금액


async def check_risk(
    session: AsyncSession,
    settings: Settings,
    *,
    side: str,
    market: str,
    krw_balance: float,
    intended_amount_krw: float,
) -> RiskCheck:
    """매매 전 리스크 검증.

    검증 항목:
    1. 1회 매매 금액 상한 (총 KRW 잔고의 N%)
    2. 일일 매매 횟수 제한
    3. 최소 KRW 잔고 유지
    """
    max_per_trade_pct = settings.risk_max_per_trade_pct
    max_daily_trades = settings.risk_max_daily_trades
    min_krw_reserve = settings.risk_min_krw_reserve

    # 1) 최소 잔고 유지
    available = krw_balance - min_krw_reserve
    if available <= 0:
        return RiskCheck(
            allowed=False,
            reason=f"KRW 잔고 부족 ({krw_balance:,.0f} < 최소유지 {min_krw_reserve:,.0f})",
            max_amount_krw=0,
        )

    # 2) 1회 매매 금액 상한
    max_amount = krw_balance * (max_per_trade_pct / 100)
    if intended_amount_krw > max_amount:
        return RiskCheck(
            allowed=False,
            reason=(
                f"1회 매매 상한 초과 "
                f"({intended_amount_krw:,.0f} > {max_amount:,.0f}, "
                f"잔고의 {max_per_trade_pct}%)"
            ),
            max_amount_krw=max_amount,
        )

    # 3) 일일 매매 횟수
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_count_result = await session.execute(
        select(func.count(Trade.id)).where(
            Trade.created_at >= today_start,
        )
    )
    daily_count = daily_count_result.scalar_one()

    if daily_count >= max_daily_trades:
        return RiskCheck(
            allowed=False,
            reason=f"일일 매매 횟수 초과 ({daily_count}/{max_daily_trades})",
            max_amount_krw=0,
        )

    # 사용 가능 금액 = min(의도 금액, 상한, 가용 잔고)
    final_amount = min(intended_amount_krw, max_amount, available)

    return RiskCheck(
        allowed=True,
        reason="리스크 검증 통과",
        max_amount_krw=final_amount,
    )

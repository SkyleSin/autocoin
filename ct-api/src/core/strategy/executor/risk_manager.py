"""리스크 관리 — 매매 전 안전 검증."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from utils.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskCheck:
    """리스크 검증 결과."""

    allowed: bool
    reason: str
    max_amount_krw: float  # 이번 매매에 사용 가능한 최대 금액


async def check_risk(
    settings: Settings,
    *,
    side: str,
    market: str,
    krw_balance: float,
    intended_amount_krw: float,
    daily_trade_count: int | None = None,
) -> RiskCheck:
    """매매 전 리스크 검증.

    검증 항목:
    1. 최소 KRW 잔고 유지
    2. 일일 매매 횟수 제한
    3. 사용 가능 금액 산출

    daily_trade_count를 전달하지 않으면 API로 조회.
    """
    max_daily_trades = settings.risk_max_daily_trades
    min_krw_reserve = settings.risk_min_krw_reserve

    # 1) 최소 잔고 유지
    available = krw_balance - min_krw_reserve
    if available <= 0:
        return RiskCheck(
            allowed=False,
            reason=(
                f"KRW 잔고 부족 "
                f"({krw_balance:,.0f} < 최소유지 {min_krw_reserve:,.0f})"
            ),
            max_amount_krw=0,
        )

    # 2) 일일 매매 횟수
    if daily_trade_count is None:
        from infra.api.trade import get_daily_trade_count
        daily_trade_count = await get_daily_trade_count()

    if daily_trade_count >= max_daily_trades:
        return RiskCheck(
            allowed=False,
            reason=(
                f"일일 매매 횟수 초과 "
                f"({daily_trade_count}/{max_daily_trades})"
            ),
            max_amount_krw=0,
        )

    # 사용 가능 금액 = min(의도 금액, 가용 잔고)
    final_amount = min(intended_amount_krw, available)

    return RiskCheck(
        allowed=True,
        reason="리스크 검증 통과",
        max_amount_krw=final_amount,
    )

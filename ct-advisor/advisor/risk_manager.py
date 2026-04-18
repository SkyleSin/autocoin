"""리스크 관리 — 매매 전 안전 검증 (유저별 설정 지원)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from infra.api.trade import get_daily_trade_count
from utils.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskCheck:
    allowed: bool
    reason: str


async def check_risk(
    settings: Settings,
    *,
    side: str,
    market: str,
    user_data: dict | None = None,
) -> RiskCheck:
    """매매 전 리스크 검증.

    검증 항목:
    1. 일일 매매 횟수 제한

    유저별 risk_settings가 있으면 오버라이드, 없으면 기본 Settings 사용.
    잔고 기반 검증은 ct-worker 실행 시점에 처리.
    """
    # 유저별 오버라이드
    risk = {}
    if user_data:
        risk = user_data.get("risk_settings") or {}

    max_daily = risk.get("daily_trade_limit", settings.risk_max_daily_trades)

    # 일일 매매 횟수 체크
    daily_count = await get_daily_trade_count()
    if daily_count >= max_daily:
        return RiskCheck(
            allowed=False,
            reason=f"일일 매매 횟수 초과 ({daily_count}/{max_daily})",
        )

    return RiskCheck(allowed=True, reason="리스크 검증 통과")

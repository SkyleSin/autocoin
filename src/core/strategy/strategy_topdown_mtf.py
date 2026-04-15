"""탑다운 MTF 전략 — 4h 추세 → 1h 신뢰도 → 15m 트리거.

4시간봉으로 추세 방향을 결정하고,
1시간봉 지표로 신뢰도를 가감한 뒤,
15분봉에서 진입 시점을 포착하는 탑다운 전략.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseStrategy, StrategyResult, register_strategy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from utils.config import Settings


@register_strategy
class StrategyTopdownMTF(BaseStrategy):
    name = "topdown_mtf"
    description = "4h 추세 → 1h 신뢰도 가감 → 15m 진입 트리거"

    def required_timeframes(self) -> list[str]:
        return ["15m", "1h", "4h"]

    async def evaluate(
        self,
        session: AsyncSession,
        market: str,
        settings: Settings,
    ) -> StrategyResult:
        """탑다운 MTF 평가.

        1단계: 4h 추세 필터 — 방향 결정 (UP/DOWN/NEUTRAL)
        2단계: 1h 지표 — 신뢰도 가감
        3단계: 15m 지표 — 트리거 시점
        """
        # TODO: 구현 예정
        return StrategyResult(
            signal_type="HOLD",
            confidence=0.0,
            reasons=["전략 미구현"],
        )

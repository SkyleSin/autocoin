"""패턴 캐시 동기화 — DB pattern_configs → 메모리 캐시."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.pattern_config import PatternConfig

logger = logging.getLogger(__name__)


async def sync_pattern_cache(session: AsyncSession) -> None:
    """DB pattern_configs → 메모리 캐시 동기화."""
    from core.analysis.patterns import set_active_patterns
    from core.strategy.executor.position_sizing import set_pattern_sizing_redis

    rows = (await session.execute(select(PatternConfig))).scalars().all()

    if not rows:
        logger.info("pattern_configs empty, using fallback defaults")
        return

    active_names = {r.name for r in rows if r.active}
    allocation = {r.name: r.buy_allocation_pct for r in rows}
    sell_rec = {r.name: r.sell_recommendation_pct for r in rows}

    set_active_patterns(active_names)
    await set_pattern_sizing_redis(allocation, sell_rec)
    logger.info(
        "pattern cache synced: %d active / %d total",
        len(active_names), len(rows),
    )

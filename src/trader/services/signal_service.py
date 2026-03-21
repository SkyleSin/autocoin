from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trader.models import SignalEvent
from trader.schemas.signal import SignalCreate


class SignalService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, body: SignalCreate) -> SignalEvent:
        row = SignalEvent(
            source=body.source,
            market=body.market,
            side=body.side,
            reason=body.reason,
            payload=body.payload,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def list_recent(self, *, limit: int) -> list[SignalEvent]:
        result = await self._session.execute(
            select(SignalEvent).order_by(SignalEvent.created_at.desc()).limit(limit),
        )
        return list(result.scalars().all())

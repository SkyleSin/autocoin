from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autocoin.api.deps import get_db, require_api_token
from autocoin.api.schemas import SignalCreate, SignalRead
from autocoin.db.models import SignalEvent

router = APIRouter(prefix="/signals", tags=["signals"])


@router.post(
    "",
    dependencies=[Depends(require_api_token)],
    response_model=SignalRead,
)
async def create_signal(
    body: SignalCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SignalEvent:
    row = SignalEvent(
        source=body.source,
        market=body.market,
        side=body.side,
        reason=body.reason,
        payload=body.payload,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get(
    "",
    dependencies=[Depends(require_api_token)],
    response_model=list[SignalRead],
)
async def list_signals(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SignalEvent]:
    result = await db.execute(
        select(SignalEvent).order_by(SignalEvent.created_at.desc()).limit(limit),
    )
    return list(result.scalars().all())

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from autocoin.api.deps import get_signal_service, require_api_token
from autocoin.schemas.signal import SignalCreate, SignalRead
from autocoin.services.signal_service import SignalService

router = APIRouter(prefix="/signals", tags=["signals"])


@router.post(
    "",
    dependencies=[Depends(require_api_token)],
    response_model=SignalRead,
)
async def create_signal(
    body: SignalCreate,
    svc: Annotated[SignalService, Depends(get_signal_service)],
) -> SignalRead:
    row = await svc.create(body)
    return SignalRead.model_validate(row)


@router.get(
    "",
    dependencies=[Depends(require_api_token)],
    response_model=list[SignalRead],
)
async def list_signals(
    svc: Annotated[SignalService, Depends(get_signal_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SignalRead]:
    rows = await svc.list_recent(limit=limit)
    return [SignalRead.model_validate(r) for r in rows]

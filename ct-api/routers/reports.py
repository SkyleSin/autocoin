"""리포트 라우터 — 수익률 리포트 / 상세 리포트 생성."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from services.report_service import (
    generate_detail_report,
    generate_report,
    generate_signal_summary,
)
from sqlalchemy.ext.asyncio import AsyncSession
from utils.deps import get_db

router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateBody(BaseModel):
    accounts: list[dict] | None = None
    ticker_prices: dict[str, float] | None = None


@router.post("/generate")
async def generate(
    body: GenerateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    text = await generate_report(
        db,
        accounts=body.accounts,
        ticker_prices=body.ticker_prices,
    )
    return {"text": text}


@router.post("/generate-detail")
async def generate_detail(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    pages = await generate_detail_report(db)
    return {"pages": pages}


class SignalSummaryBody(BaseModel):
    hours: int = 12


@router.post("/signal-summary")
async def signal_summary(
    body: SignalSummaryBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    text = await generate_signal_summary(db, hours=body.hours)
    return {"text": text}

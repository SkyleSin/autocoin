"""마켓 목록 CRUD 라우터."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.market import Market
from utils.deps import get_db

router = APIRouter(prefix="/api/markets", tags=["markets"])


class MarketItem(BaseModel):
    market: str
    korean_name: str
    english_name: str


class SyncMarketsRequest(BaseModel):
    items: list[MarketItem]


@router.get("")
async def list_markets(
    keyword: str = Query("", description="검색 키워드"),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """KRW 마켓 목록 조회. keyword로 필터."""
    q = select(Market).where(Market.is_active.is_(True))
    rows = (await db.execute(q.order_by(Market.market))).scalars().all()

    result = [_to_dict(r) for r in rows]
    if keyword:
        kw = keyword.upper()
        result = [
            m for m in result
            if kw in m["market"].upper()
            or kw in m["korean_name"]
            or kw in m["english_name"].upper()
        ]
    return result


@router.put("/sync", status_code=status.HTTP_200_OK)
async def sync_markets(
    req: SyncMarketsRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """빗썸 마켓 목록 동기화 — upsert + 비활성 처리."""
    incoming = {item.market for item in req.items}

    existing = (await db.execute(select(Market))).scalars().all()
    existing_map = {r.market: r for r in existing}

    upserted = 0
    deactivated = 0

    for item in req.items:
        if item.market in existing_map:
            row = existing_map[item.market]
            row.korean_name = item.korean_name
            row.english_name = item.english_name
            row.is_active = True
        else:
            db.add(Market(
                market=item.market,
                korean_name=item.korean_name,
                english_name=item.english_name,
                is_active=True,
            ))
        upserted += 1

    # 빗썸에서 사라진 마켓 비활성
    for market_code, row in existing_map.items():
        if market_code not in incoming and row.is_active:
            row.is_active = False
            deactivated += 1

    await db.commit()
    return {"upserted": upserted, "deactivated": deactivated}


def _to_dict(r: Market) -> dict[str, Any]:
    return {
        "market": r.market,
        "korean_name": r.korean_name,
        "english_name": r.english_name,
        "is_active": r.is_active,
    }

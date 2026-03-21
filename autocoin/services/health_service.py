from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class HealthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check_database(self) -> None:
        await self._session.execute(text("SELECT 1"))

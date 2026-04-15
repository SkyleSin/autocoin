"""텔레그램 봇 양방향 테스트 — 폴러 실행 후 명령어 수신 대기."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.base import Base
from telegram.bot import send_message
from telegram.poller import telegram_poller
from utils.config import Settings


async def main() -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요")
        return

    # 시작 메시지
    await send_message(
        token, chat_id,
        "🤖 autocoin 봇 테스트 시작!\n/help 를 입력해보세요.\n\n30초 후 자동 종료됩니다.",
    )

    print("텔레그램 폴러 시작 (30초 후 자동 종료)")
    print("텔레그램에서 /help, /assets, /balance, /risk 등을 입력해보세요")

    # 30초간 폴링
    poller_task = asyncio.create_task(telegram_poller(sf, settings))
    try:
        await asyncio.wait_for(asyncio.shield(poller_task), timeout=30)
    except TimeoutError:
        poller_task.cancel()
        try:
            await poller_task
        except asyncio.CancelledError:
            pass

    await send_message(token, chat_id, "🔚 테스트 종료")
    print("\n테스트 완료")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

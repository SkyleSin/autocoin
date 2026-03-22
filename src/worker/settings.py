from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AUTOCOIN_WORKER_",
        extra="ignore",
    )

    count: int = Field(default=15, description="동시에 큐에서 꺼내 처리하는 워커 태스크 수")
    poll_interval_sec: float = Field(
        default=1.0,
        description="Ready 없을 때 프로듀서 대기(초)",
    )
    claim_limit: int = Field(default=200, description="한 번에 DB에서 가져올 Ready 상한")

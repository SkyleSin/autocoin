from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root() -> Path:
    """`pyproject.toml` 이 있는 디렉터리. 없으면 cwd."""
    here = Path(__file__).resolve().parent
    for p in [here, *here.parents]:
        if (p / "pyproject.toml").is_file():
            return p
    return Path.cwd()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="", validation_alias="DATABASE_URL")

    bithumb_api_key: str = Field(default="", validation_alias="BITHUMB_API_KEY")
    bithumb_secret_key: str = Field(default="", validation_alias="BITHUMB_SECRET_KEY")
    bithumb_base_url: str = Field(
        default="https://api.bithumb.com",
        validation_alias="BITHUMB_BASE_URL",
    )

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_dir: str = Field(default="./log", validation_alias="LOG_DIR")
    log_file: str = Field(default="", validation_alias="LOG_FILE")
    log_max_bytes: int = Field(default=10_485_760, validation_alias="LOG_MAX_BYTES")
    log_backup_count: int = Field(default=5, validation_alias="LOG_BACKUP_COUNT")
    log_json_console: bool = Field(default=False, validation_alias="LOG_JSON_CONSOLE")

    @field_validator("database_url")
    @classmethod
    def strip_database_url(cls, v: str) -> str:
        return v.strip()

    @field_validator("log_json_console", mode="before")
    @classmethod
    def parse_log_json_console(cls, v: object) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

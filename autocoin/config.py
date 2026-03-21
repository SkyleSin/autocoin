from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수 / `.env`에서 로드."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bithumb_api_key: str = ""
    bithumb_secret_key: str = ""
    bithumb_base_url: str = "https://api.bithumb.com"

    # PostgreSQL (asyncpg). 예: postgresql+asyncpg://user:***REMOVED***@localhost:5432/autocoin
    database_url: str = ""
    db_echo_sql: bool = False

    # 설정 시 보호된 엔드포인트에 `Authorization: Bearer <토큰>` 필요
    api_auth_token: str = ""

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @field_validator("database_url")
    @classmethod
    def strip_database_url(cls, v: str) -> str:
        return v.strip()

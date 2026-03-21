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

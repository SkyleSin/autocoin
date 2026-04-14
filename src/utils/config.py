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
    bithumb_max_requests_per_sec: int = Field(
        default=15,
        ge=0,
        description="빗썸(공개) REST 초당 최대 호출 수(전 워커 합산). 0이면 제한 없음.",
        validation_alias="BITHUMB_MAX_REQUESTS_PER_SEC",
    )

    job_poll_interval_sec: float = Field(
        default=1.0,
        validation_alias="AUTOCOIN_JOB_POLL_INTERVAL_SEC",
    )
    job_claim_limit: int = Field(
        default=200,
        validation_alias="AUTOCOIN_JOB_CLAIM_LIMIT",
    )
    job_worker_count: int = Field(
        default=4,
        ge=1,
        validation_alias="AUTOCOIN_JOB_WORKER_COUNT",
    )
    job_retry_max: int = Field(
        default=3,
        ge=1,
        le=3,
        description="재시도 가능 실패 시 retry_cnt가 이 값 이상이면 Error로 종료.",
        validation_alias="AUTOCOIN_JOB_RETRY_MAX",
    )

    telegram_bot_token: str = Field(
        default="",
        validation_alias="TELEGRAM_BOT_TOKEN",
    )
    telegram_chat_id: str = Field(
        default="",
        validation_alias="TELEGRAM_CHAT_ID",
    )

    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias="OLLAMA_BASE_URL",
    )
    ollama_model: str = Field(
        default="gemma3:4b",
        validation_alias="OLLAMA_MODEL",
    )
    ollama_num_gpu: int = Field(
        default=0,
        ge=0,
        description="Ollama GPU 레이어 수. 0이면 CPU 전용.",
        validation_alias="OLLAMA_NUM_GPU",
    )
    ollama_timeout: float = Field(
        default=60.0,
        description="Ollama 응답 타임아웃(초).",
        validation_alias="OLLAMA_TIMEOUT",
    )

    deepseek_api_key: str = Field(
        default="",
        validation_alias="DEEPSEEK_API_KEY",
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_BASE_URL",
    )
    deepseek_model: str = Field(
        default="deepseek-reasoner",
        description="deepseek-chat (V3) 또는 deepseek-reasoner (R1)",
        validation_alias="DEEPSEEK_MODEL",
    )
    deepseek_timeout: float = Field(
        default=120.0,
        description="DeepSeek 응답 타임아웃(초). R1은 추론 시간이 길어 넉넉하게.",
        validation_alias="DEEPSEEK_TIMEOUT",
    )

    # 런타임 설정 기본값 (DB에 저장된 값이 있으면 서버 시작 시 덮어씀)
    alert_remind_interval_sec: float = 14400.0  # 4시간 (재알림 + CHANGED 쿨다운)
    risk_max_per_trade_pct: float = 15.0
    risk_max_per_coin_pct: float = 25.0  # 코인당 최대 투자 비율 (총 자본 대비 %)
    risk_max_daily_trades: int = 30
    risk_min_krw_reserve: float = 100_000.0

    # 긴급 매도 안전장치
    safety_monitor_interval_sec: float = Field(
        default=60.0,
        description="안전장치 감시 주기(초).",
        validation_alias="AUTOCOIN_SAFETY_INTERVAL_SEC",
    )
    safety_breach_immediate_pct: float = Field(
        default=5.0,
        description="즉시 LLM 트리거 임계값 (지지선 대비 이탈 %).",
        validation_alias="AUTOCOIN_SAFETY_BREACH_IMMEDIATE_PCT",
    )
    safety_breach_consecutive_pct: float = Field(
        default=1.5,
        description="연속 감시 임계값 (지지선 대비 이탈 %).",
        validation_alias="AUTOCOIN_SAFETY_BREACH_CONSECUTIVE_PCT",
    )
    safety_breach_consecutive_count: int = Field(
        default=5,
        ge=1,
        description="연속 이탈 횟수 (이 횟수 도달 시 LLM 트리거).",
        validation_alias="AUTOCOIN_SAFETY_BREACH_CONSECUTIVE_COUNT",
    )
    safety_cooldown_sec: float = Field(
        default=300.0,
        description="긴급 판단 후 동일 마켓 쿨다운(초).",
        validation_alias="AUTOCOIN_SAFETY_COOLDOWN_SEC",
    )

    candle_interval_sec: float = Field(
        default=300.0,
        description="캔들 수집 주기(초). 5분봉 기준 300.",
        validation_alias="AUTOCOIN_CANDLE_INTERVAL_SEC",
    )
    candle_unit: int = Field(
        default=5,
        description="분봉 단위 (1, 3, 5, 10, 15, 30, 60, 240).",
        validation_alias="AUTOCOIN_CANDLE_UNIT",
    )
    candle_count: int = Field(
        default=1,
        ge=1,
        le=200,
        description="사이클당 요청 캔들 개수.",
        validation_alias="AUTOCOIN_CANDLE_COUNT",
    )
    candle_ref_timeframes: str = Field(
        default="1h,4h",
        description="참�� 타임프레임 (쉼표 구분). 추세 확인용으로만 수집, 분석 체이닝 안 함.",
        validation_alias="AUTOCOIN_CANDLE_REF_TIMEFRAMES",
    )
    pattern_markets: str = Field(
        default="KRW-BTC,KRW-ETH",
        description="패턴 분�� 대상 마켓 (쉼표 구분). 이 외 마켓은 패턴 트리거 미적용.",
        validation_alias="AUTOCOIN_PATTERN_MARKETS",
    )

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_dir: str = Field(default="./log", validation_alias="LOG_DIR")
    log_file: str = Field(
        default="autocoin.log",
        validation_alias="LOG_FILE",
        description="파일 로그 파일명(비우면 파일 핸들러 비활성).",
    )
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

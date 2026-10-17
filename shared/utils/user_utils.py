"""유저 설정 유틸리티 — dict 기반 Settings 생성 (DB 접근 없음)."""

from __future__ import annotations

from utils.config import Settings
from utils.crypto import decrypt


def get_user_settings_from_dict(
    user_data: dict, base_settings: Settings,
) -> Settings:
    """API 응답(dict)에서 유저별 Settings 생성."""
    encrypt_key = base_settings.encrypt_key
    overrides: dict = {}

    api_key = user_data.get("bithumb_api_key", "")
    secret_key = user_data.get("bithumb_secret_key", "")
    if api_key:
        overrides["bithumb_api_key"] = decrypt(api_key, encrypt_key)
    if secret_key:
        overrides["bithumb_secret_key"] = decrypt(secret_key, encrypt_key)

    return base_settings.model_copy(update=overrides)

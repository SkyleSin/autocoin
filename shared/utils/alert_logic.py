"""알림 판단 로직 — 순수 함수, DB 의존성 없음."""

from __future__ import annotations

import json
from datetime import UTC, datetime

_PASS_CONFIDENCE_THRESHOLD = 0.15


class AlertAction:
    """알림 판단 결과."""

    NEW = "new"
    CHANGED = "changed"
    REMIND = "remind"
    SKIP = "skip"
    CLEARED = "cleared"
    PASSED = "passed"


def pass_fingerprint(indicators: dict | None, confidence: float) -> str:
    """패스 비교용 핑거프린트. S/R 시그널 + confidence 기반."""
    sr = "NONE"
    if indicators:
        sr_data = indicators.get("support_resistance")
        if isinstance(sr_data, dict):
            sr = sr_data.get("signal", "NONE")
    return json.dumps({"sr": sr, "conf": round(confidence, 2)}, sort_keys=True)


def should_clear_pass(old_fp_str: str | None, new_fp_str: str) -> bool:
    """패스 해제 판단. S/R 변경 또는 confidence 유의미 상승 시 True."""
    if not old_fp_str:
        return True
    try:
        old = json.loads(old_fp_str)
        new = json.loads(new_fp_str)
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(old, dict) or not isinstance(new, dict):
        return True
    if old.get("sr") != new.get("sr"):
        return True
    old_conf = old.get("conf", 0.0)
    new_conf = new.get("conf", 0.0)
    return (new_conf - old_conf) >= _PASS_CONFIDENCE_THRESHOLD


def decide_alert_action(
    existing: dict | None,
    signal_type: str,
    confidence: float,
    reasons: list[str],
    indicators: dict | None,
    remind_interval_sec: float = 600.0,
) -> tuple[str, dict]:
    """알림 판단 순수 함수.

    Returns:
        (action, updates) — updates에 "_create" 키면 새 레코드 생성
    """
    from utils.alert_logic_decide import decide_with_existing

    if signal_type == "HOLD":
        return AlertAction.SKIP, {}

    now = datetime.now(UTC)
    now_iso = now.isoformat()
    reasons_json = json.dumps(reasons, ensure_ascii=False)

    if existing is None:
        return AlertAction.NEW, {
            "_create": True,
            "signal_type": signal_type,
            "confidence": confidence,
            "reasons": reasons_json,
            "indicators": indicators,
            "last_alerted_at": now_iso,
        }

    return decide_with_existing(
        existing, signal_type, confidence, reasons_json,
        indicators, remind_interval_sec, now, now_iso,
    )

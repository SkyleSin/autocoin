"""알림 판단 로직 — 기존 알림이 있을 때의 분기 처리."""

from __future__ import annotations

from datetime import UTC, datetime

from utils.alert_logic import AlertAction, pass_fingerprint, should_clear_pass

_PASS_CONFIDENCE_THRESHOLD = 0.15


def _parse_last_alerted(raw: str | datetime | None) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def decide_with_existing(
    ex: dict, signal_type: str, confidence: float, reasons_json: str,
    indicators: dict | None, remind_interval_sec: float,
    now: datetime, now_iso: str,
) -> tuple[str, dict]:
    """기존 알림 대비 판단. alert_logic.decide_alert_action에서 호출."""
    ex_signal, ex_confidence = ex.get("signal_type"), ex.get("confidence", 0.0)
    ex_indicators = ex.get("indicators")
    last_alerted = _parse_last_alerted(ex.get("last_alerted_at"))
    base = {"confidence": confidence, "reasons": reasons_json, "indicators": indicators}

    if ex.get("acted_at") is not None:
        result = _handle_acted(
            ex_signal, signal_type, ex_indicators, ex_confidence,
            indicators, confidence, base, now_iso,
        )
        if result:
            return result

    if ex_signal != signal_type:
        return _handle_signal_change(
            signal_type, base, now, last_alerted, remind_interval_sec, now_iso,
        )

    if ex.get("passed_at") is not None:
        return _handle_passed(ex.get("passed_reasons"), indicators, confidence, base, now_iso)

    if (confidence - ex_confidence) >= _PASS_CONFIDENCE_THRESHOLD:
        return AlertAction.CHANGED, {**base, "last_alerted_at": now_iso}

    elapsed = (now - last_alerted).total_seconds()
    if elapsed >= remind_interval_sec:
        return AlertAction.REMIND, {**base, "last_alerted_at": now_iso}

    return AlertAction.SKIP, {}


def _handle_acted(
    ex_signal: str | None, signal_type: str,
    ex_indicators: dict | None, ex_confidence: float,
    indicators: dict | None, confidence: float,
    base: dict, now_iso: str,
) -> tuple[str, dict] | None:
    if ex_signal == signal_type:
        old_fp = pass_fingerprint(ex_indicators, ex_confidence)
        new_fp = pass_fingerprint(indicators, confidence)
        if not should_clear_pass(old_fp, new_fp):
            return AlertAction.SKIP, {}
        return AlertAction.CHANGED, {**base, "acted_at": None, "last_alerted_at": now_iso}
    return None


def _handle_signal_change(
    signal_type: str, base: dict,
    now: datetime, last_alerted: datetime,
    remind_interval_sec: float, now_iso: str,
) -> tuple[str, dict]:
    elapsed = (now - last_alerted).total_seconds()
    updates = {**base, "signal_type": signal_type, "acted_at": None}
    if elapsed < remind_interval_sec:
        return AlertAction.SKIP, updates
    updates["last_alerted_at"] = now_iso
    return AlertAction.CHANGED, updates


def _handle_passed(
    passed_reasons: str | None, indicators: dict | None,
    confidence: float, base: dict, now_iso: str,
) -> tuple[str, dict]:
    new_fp = pass_fingerprint(indicators, confidence)
    if not should_clear_pass(passed_reasons, new_fp):
        return AlertAction.PASSED, {}
    return AlertAction.CHANGED, {
        **base, "passed_at": None, "passed_reasons": None, "last_alerted_at": now_iso,
    }

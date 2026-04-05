"""루트 로거: 콘솔(텍스트 또는 JSON), 선택적 회전 파일."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

from utils.config import Settings

_KST = timezone(timedelta(hours=9))

_RESERVED_LOGRECORD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "thread",
        "threadName",
        "exc_info",
        "exc_text",
        "stack_info",
        "taskName",
    },
)


class KSTFormatter(logging.Formatter):
    def converter(self, timestamp: float) -> datetime:
        dt = datetime.fromtimestamp(timestamp, tz=UTC)
        return dt.astimezone(_KST)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = self.converter(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


class JSONConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "@timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "log.level": record.levelname,
            "log.logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["error.type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["error.message"] = str(record.exc_info[1]) if record.exc_info[1] else None
            payload["error.stack_trace"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOGRECORD_KEYS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def _resolve_level(level: str | int | None) -> int:
    if level is None:
        return logging.INFO
    if isinstance(level, int):
        return level
    return getattr(logging, level.upper(), logging.INFO)


def _resolved_file_path(log_dir: str, log_file: str) -> str:
    return os.path.abspath(os.path.join(log_dir, log_file))


def _root_has_rotating_handler_for(root: logging.Logger, path: str) -> bool:
    want = os.path.normpath(path)
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler):
            bf = getattr(h, "baseFilename", None)
            if bf is not None and os.path.normpath(bf) == want:
                return True
    return False


def _attach_rotating_file_handler(
    root: logging.Logger,
    *,
    log_dir: str,
    log_file: str,
    max_bytes: int,
    backup: int,
) -> None:
    path = _resolved_file_path(log_dir, log_file)
    if _root_has_rotating_handler_for(root, path):
        return
    os.makedirs(log_dir, exist_ok=True)
    fh = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup,
        encoding="utf-8",
    )
    fh.setFormatter(
        KSTFormatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ),
    )
    root.addHandler(fh)


def setup_logging(
    *,
    settings: Settings | None = None,
    level: str | int | None = None,
    json_console: bool | None = None,
    force: bool = False,
) -> None:
    root = logging.getLogger()

    if settings is not None:
        eff_level = level if level is not None else settings.log_level
        use_json = json_console if json_console is not None else settings.log_json_console
        log_dir = settings.log_dir
        log_file = settings.log_file.strip()
        max_bytes = settings.log_max_bytes
        backup = settings.log_backup_count
    else:
        eff_level = level if level is not None else "INFO"
        use_json = json_console if json_console is not None else False
        log_dir = "./log"
        log_file = ""
        max_bytes = 10_485_760
        backup = 5

    if root.handlers and not force:
        root.setLevel(_resolve_level(eff_level))
        # uvicorn 등이 이미 StreamHandler를 붙인 뒤에는 파일 핸들러만 없을 수 있음.
        if log_file:
            try:
                _attach_rotating_file_handler(
                    root,
                    log_dir=log_dir,
                    log_file=log_file,
                    max_bytes=max_bytes,
                    backup=backup,
                )
            except OSError as e:
                root.warning("파일 로그 비활성: %s", e)
        return

    root.setLevel(_resolve_level(eff_level))
    if force:
        root.handlers.clear()

    if use_json:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(JSONConsoleFormatter())
    else:
        console = logging.StreamHandler(sys.stdout)
        fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        console.setFormatter(KSTFormatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(console)

    if log_file:
        try:
            _attach_rotating_file_handler(
                root,
                log_dir=log_dir,
                log_file=log_file,
                max_bytes=max_bytes,
                backup=backup,
            )
        except OSError as e:
            root.warning("파일 로그 비활성: %s", e)

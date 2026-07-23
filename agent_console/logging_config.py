from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_LEVEL_ENV = "AGENT_CONSOLE_LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"

_LOG_CONFIGURED = False


def _safe_parse_int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

SECRET_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(sk-(?:[a-z0-9]+-)*)[a-z0-9]{16,}"), r"\1****"),
    (re.compile(r"(?i)(gh[opsur])_[a-zA-Z0-9]{36,}"), r"\1_****"),
    (re.compile(r"(?i)github_pat_[a-zA-Z0-9]{36,}"), "github_pat_****"),
    (re.compile(r"(?i)(xox[barps])-[a-zA-Z0-9]{10,}"), r"\1-****"),
    (re.compile(r"(?i)(bearer\s+)[a-z0-9._-]{16,}"), r"\1****"),
    (re.compile(r"(?i)(api[_-]?key|secret|password|credential|auth[_-]?header)\s*[:=]\s*[^\s*]+\S*"), r"\1=****"),
]


def redact_secrets(message: str) -> str:
    for pattern, replacement in SECRET_RULES:
        message = pattern.sub(replacement, message)
    return message


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {_redact_value(k): _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        else:
            record.msg = _redact_value(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = _redact_value(record.args)
            else:
                record.args = tuple(_redact_value(a) for a in record.args)
        if record.exc_info and not record.exc_text:
            record.exc_text = redact_secrets(
                "".join(traceback.format_exception(*record.exc_info))
            )
        elif record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        created = datetime.fromtimestamp(record.created, tz=timezone.utc)
        obj: dict[str, Any] = {
            "timestamp": created.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
        }
        if record.exc_info and isinstance(record.exc_info, tuple) and record.exc_info[1]:
            obj["exception"] = redact_secrets(str(record.exc_info[1]))
        return json.dumps(obj, ensure_ascii=False, default=str)


def resolve_log_dir() -> Path:
    raw = os.getenv("AGENT_CONSOLE_LOG_DIR", "")
    if raw:
        return Path(raw).expanduser()
    state_dir = os.getenv("AGENT_CONSOLE_STATE_DIR", "")
    if state_dir:
        return Path(state_dir).expanduser() / "logs"
    return Path.home() / ".local" / "share" / "agent-console" / "logs"


def _clamp_positive(value: int, name: str, default: int) -> int:
    if value < 1:
        return default
    return value


def configure_logging(
    *,
    log_dir: Path | None = None,
    retention_days: int | None = None,
    backup_count: int | None = None,
) -> None:
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return

    level_name = os.getenv(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL).upper()
    level = getattr(logging, level_name, logging.INFO)

    text_fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    root = logging.getLogger()
    root.setLevel(level)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(text_fmt)
    stdout_handler.addFilter(SecretRedactionFilter())
    root.addHandler(stdout_handler)

    json_handler = _create_json_file_handler(
        log_dir=log_dir,
        level=level,
        retention_days=retention_days,
        backup_count=backup_count,
    )
    if json_handler is not None:
        json_handler.addFilter(SecretRedactionFilter())
        root.addHandler(json_handler)

    _LOG_CONFIGURED = True

    if json_handler is not None and json_handler.baseFilename:
        retention = retention_days if retention_days is not None else _safe_parse_int(
            os.getenv("AGENT_CONSOLE_LOG_RETENTION_DAYS"), 395
        )
        retention = _clamp_positive(retention, "log_retention_days", 395)
        _prune_log_dir(Path(json_handler.baseFilename).parent, retention)


def _create_json_file_handler(
    log_dir: Path | None,
    level: int,
    retention_days: int | None,
    backup_count: int | None = None,
) -> logging.Handler | None:
    resolved = log_dir or resolve_log_dir()
    if resolved is None:
        return None

    try:
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    except (OSError, PermissionError):
        return None

    log_path = resolved / "agent-console.jsonl"
    bc_raw = backup_count if backup_count is not None else _safe_parse_int(
        os.getenv("AGENT_CONSOLE_LOG_BACKUP_COUNT"), 400
    )
    bc = _clamp_positive(bc_raw, "log_backup_count", 400)

    try:
        handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_path),
            when="midnight",
            backupCount=bc,
            encoding="utf-8",
        )
    except (OSError, PermissionError):
        return None

    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    return handler


def _prune_log_dir(log_dir: Path, retention_days: int) -> None:
    if retention_days < 1:
        return
    cutoff = time.time() - retention_days * 86400
    try:
        for entry in log_dir.iterdir():
            if entry.is_file() and (entry.name.endswith(".jsonl") or ".jsonl." in entry.name):
                mtime = entry.stat().st_mtime
                if mtime < cutoff:
                    entry.unlink()
    except (OSError, PermissionError):
        pass


def prune_logs(log_dir: str | Path | None = None, retention_days: int = 395) -> None:
    resolved = Path(log_dir).expanduser() if log_dir else resolve_log_dir()
    _prune_log_dir(resolved, retention_days)


def get_logger(name: str) -> logging.Logger:
    if not _LOG_CONFIGURED:
        configure_logging()
    return logging.getLogger(name)


def reset_logging() -> None:
    global _LOG_CONFIGURED
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
    for ftr in list(root.filters):
        root.removeFilter(ftr)
    _LOG_CONFIGURED = False


def configure_uvicorn_logging() -> None:
    root = logging.getLogger()
    root_handlers = [h for h in root.handlers]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
        uvi = logging.getLogger(name)
        uvi.handlers.clear()
        for h in root_handlers:
            uvi.addHandler(h)
        uvi.propagate = False


def read_logging_config_json(path: str | Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def assert_logging_config_parity(config_path: str | Path) -> None:
    cfg = read_logging_config_json(config_path)
    assert "version" in cfg, "logging-config.json must have a version field"
    assert cfg["version"] == 1, "logging-config.json version must be 1"
    assert "formatters" in cfg, "logging-config.json must have formatters"
    assert "handlers" in cfg, "logging-config.json must have handlers"
    assert "root" in cfg, "logging-config.json must have a root logger config"
    root_handlers = cfg.get("root", {}).get("handlers", [])
    assert len(root_handlers) > 0, "root logger must have at least one handler"
    for hname in root_handlers:
        assert hname in cfg["handlers"], (
            f"root logger handler '{hname}' must be defined in handlers"
        )

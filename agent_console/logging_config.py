from __future__ import annotations

import logging
import os
import sys

LOG_LEVEL_ENV = "AGENT_CONSOLE_LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"

_LOG_CONFIGURED = False


def configure_logging() -> None:
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return

    level_name = os.getenv(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL).upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    _LOG_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    if not _LOG_CONFIGURED:
        configure_logging()
    return logging.getLogger(name)


def reset_logging() -> None:
    global _LOG_CONFIGURED
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    _LOG_CONFIGURED = False

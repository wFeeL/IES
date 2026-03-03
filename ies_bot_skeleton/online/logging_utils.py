from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from importlib import metadata
from typing import Any


def get_logger(name: str = "ies.online", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    return logger


def project_version() -> str:
    try:
        return metadata.version("ies-bot-skeleton")
    except Exception:
        try:
            from ies_bot_skeleton import __version__

            return str(__version__)
        except Exception:
            return "0.0.0-dev"


def log_event(logger: Any, level: str, event: str, **fields: Any) -> None:
    if logger is None:
        return
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    payload.update(fields)
    log_level = getattr(logging, str(level).upper(), logging.INFO)
    logger.log(log_level, json.dumps(payload, ensure_ascii=False, sort_keys=True))


__all__ = ["get_logger", "log_event", "project_version"]

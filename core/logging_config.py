"""Central logging setup: writes logs/app.log and never swallows errors.

GUI code only ever sees a short, friendly message; full tracebacks and
ffmpeg/API details always go to the log file (spec 十二).
"""
from __future__ import annotations

import logging
import logging.handlers
import os


def setup_logging(log_dir: str = "logs", filename: str = "app.log", level=logging.INFO) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, filename)

    logger = logging.getLogger("app")
    if logger.handlers:
        return logger  # already configured (e.g. re-entrant calls / tests)

    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.WARNING)
    logger.addHandler(console_handler)

    return logger


def redact_key(key: str) -> str:
    """Never print a full API key anywhere, including logs (spec 十四)."""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"

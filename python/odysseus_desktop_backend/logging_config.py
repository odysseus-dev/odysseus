from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "odysseus_desktop_backend"


def setup_logging(profile_dir: str | Path) -> logging.Logger:
    log_dir = Path(profile_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_path = log_dir / "backend.log"
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) != log_path:
            logger.removeHandler(handler)
            handler.close()

    existing_file_handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == log_path
    ]
    if not existing_file_handlers:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(_formatter())
        logger.addHandler(file_handler)

    if not any(getattr(handler, "_odysseus_stderr", False) for handler in logger.handlers):
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(_formatter())
        stderr_handler._odysseus_stderr = True  # type: ignore[attr-defined]
        logger.addHandler(stderr_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def _formatter() -> logging.Formatter:
    return logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")

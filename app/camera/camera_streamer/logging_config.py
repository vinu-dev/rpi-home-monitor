"""
Centralized logging configuration for the camera streamer.

Sets up both console (stderr → journalctl) and file handlers so that
all log output persists across service restarts for debugging.

File logs use RotatingFileHandler to avoid filling disk:
  - /data/logs/camera.log  — main app log (5 MB × 3 = 15 MB max)
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get("CAMERA_LOG_DIR", "/data/logs"))
LOG_FILE = "camera.log"
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file (smaller disk on Zero 2W)
LOG_BACKUP_COUNT = 3  # keep 3 rotated files (15 MB total)


def configure_logging(log_level=None):
    """Configure root logger with console + rotating file handlers.

    Args:
        log_level: Override log level. Defaults to LOG_LEVEL env var
                   or WARNING for production.
    """
    if log_level is None:
        log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()

    level = getattr(logging, log_level, logging.WARNING)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # Console handler (stderr → journalctl)
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console)

    # File handler (persistent, survives restarts)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(LOG_DIR / LOG_FILE),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)
    except OSError as e:
        root.warning("Cannot create file log at %s: %s", LOG_DIR / LOG_FILE, e)

    logging.getLogger("camera-streamer").info(
        "Logging configured: level=%s, file=%s", log_level, LOG_DIR / LOG_FILE
    )

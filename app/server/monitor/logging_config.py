"""
Centralized logging configuration for the monitor server.

Sets up both console (stderr → journalctl) and file handlers so that
all log output persists across service restarts for debugging.

File logs use RotatingFileHandler to avoid filling disk:
  - /data/logs/monitor.log       — main app log (10 MB × 5 = 50 MB max)
  - /data/logs/ffmpeg/           — per-pipeline ffmpeg stderr (managed by streaming.py)

Usage:
    from monitor.logging_config import configure_logging
    configure_logging()  # call once at startup, before any getLogger()
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get("MONITOR_LOG_DIR", "/data/logs"))
LOG_FILE = "monitor.log"
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
LOG_BACKUP_COUNT = 5  # keep 5 rotated files (50 MB total)


def configure_logging(log_level=None):
    """Configure root logger with console + rotating file handlers.

    Args:
        log_level: Override log level. Defaults to LOG_LEVEL env var
                   or WARNING for production.
    """
    if log_level is None:
        log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()

    level = getattr(logging, log_level, logging.WARNING)

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)

    # Clear any existing handlers (prevents duplicates on reload)
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
        # Can't create log file (read-only FS, permissions, etc.)
        # Fall back to console-only — don't crash the app over logging
        root.warning("Cannot create file log at %s: %s", LOG_DIR / LOG_FILE, e)

    logging.getLogger("monitor").info(
        "Logging configured: level=%s, file=%s", log_level, LOG_DIR / LOG_FILE
    )

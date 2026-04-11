"""Tests for monitor.logging_config module."""

import logging
import os
from pathlib import Path
from unittest.mock import patch

from monitor.logging_config import (
    LOG_BACKUP_COUNT,
    LOG_FORMAT,
    LOG_MAX_BYTES,
    configure_logging,
)


class TestConfigureLogging:
    """Test the centralized logging setup."""

    def test_sets_root_level(self):
        configure_logging("DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_default_level_from_env(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "ERROR"}):
            configure_logging()
        root = logging.getLogger()
        assert root.level == logging.ERROR

    def test_default_level_warning(self):
        with patch.dict(os.environ, {}, clear=True):
            configure_logging()
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_adds_console_handler(self):
        configure_logging("INFO")
        root = logging.getLogger()
        console_handlers = [
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename")
        ]
        assert len(console_handlers) >= 1

    def test_adds_file_handler(self, tmp_path):
        with patch("monitor.logging_config.LOG_DIR", tmp_path):
            configure_logging("INFO")
        root = logging.getLogger()
        from logging.handlers import RotatingFileHandler

        file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) >= 1
        assert (tmp_path / "monitor.log").exists()

    def test_file_handler_failure_does_not_crash(self):
        """If log dir is not writable, fall back to console only."""
        with patch(
            "monitor.logging_config.LOG_DIR",
            Path("/nonexistent/path/that/cannot/exist"),
        ):
            configure_logging("INFO")  # should not raise
        root = logging.getLogger()
        assert len(root.handlers) >= 1  # at least console

    def test_clears_existing_handlers(self):
        """Calling configure_logging twice doesn't duplicate handlers."""
        configure_logging("INFO")
        count1 = len(logging.getLogger().handlers)
        configure_logging("INFO")
        count2 = len(logging.getLogger().handlers)
        assert count1 == count2

    def test_invalid_level_defaults_to_warning(self):
        configure_logging("NOTAVALIDLEVEL")
        root = logging.getLogger()
        assert root.level == logging.WARNING


class TestConstants:
    def test_max_bytes(self):
        assert LOG_MAX_BYTES == 10 * 1024 * 1024

    def test_backup_count(self):
        assert LOG_BACKUP_COUNT == 5

    def test_format_has_name_and_level(self):
        assert "%(name)s" in LOG_FORMAT
        assert "%(levelname)s" in LOG_FORMAT

# REQ: SWR-035; RISK: RISK-012; SEC: SC-012; TEST: TC-033
"""Unit tests for logging_config.configure_logging().

Key invariant: the picamera2 named logger must be clamped to WARNING
regardless of the root log level, so the ~6 entries/sec DEBUG flood
from picamera2 ("Execute job: ...") never reaches the journal under
active streaming. See issue #170.
"""

from __future__ import annotations

import logging


class TestPicamera2LoggerSuppression:
    def _reconfigure(self, level="WARNING"):
        """Call configure_logging() and return the picamera2 logger."""
        import importlib

        import camera_streamer.logging_config as mod

        importlib.reload(mod)
        mod.configure_logging(log_level=level)
        return logging.getLogger("picamera2")

    def test_picamera2_clamped_to_warning_at_default_level(self):
        logger = self._reconfigure("WARNING")
        assert logger.level == logging.WARNING

    def test_picamera2_clamped_to_warning_even_when_root_is_debug(self):
        """Root at DEBUG must not let picamera2 flood the journal."""
        logger = self._reconfigure("DEBUG")
        assert logger.level == logging.WARNING

    def test_picamera2_rejects_debug_records(self):
        self._reconfigure("DEBUG")
        logger = logging.getLogger("picamera2")
        assert not logger.isEnabledFor(logging.DEBUG)
        assert logger.isEnabledFor(logging.WARNING)

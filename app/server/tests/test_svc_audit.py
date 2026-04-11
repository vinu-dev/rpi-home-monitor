"""Tests for the audit logging service."""

import json
from unittest.mock import patch

from monitor.services.audit import AuditLogger


class TestAuditLoggerInit:
    """Test AuditLogger initialization."""

    def test_creates_logs_dir(self, tmp_path):
        logs_dir = tmp_path / "newlogs"
        AuditLogger(str(logs_dir))
        assert logs_dir.exists()

    def test_works_with_existing_dir(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        assert logger.logs_dir.exists()


class TestLogEvent:
    """Test writing audit events."""

    def test_log_creates_file(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("TEST_EVENT")
        assert (data_dir / "logs" / "audit.log").exists()

    def test_log_writes_json_line(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("LOGIN_SUCCESS", user="admin", ip="192.168.1.50")
        content = (data_dir / "logs" / "audit.log").read_text()
        entry = json.loads(content.strip())
        assert entry["event"] == "LOGIN_SUCCESS"
        assert entry["user"] == "admin"
        assert entry["ip"] == "192.168.1.50"

    def test_log_has_timestamp(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("TEST_EVENT")
        content = (data_dir / "logs" / "audit.log").read_text()
        entry = json.loads(content.strip())
        assert "timestamp" in entry
        assert "T" in entry["timestamp"]
        assert entry["timestamp"].endswith("Z")

    def test_log_includes_detail(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("CAMERA_PAIRED", detail="cam-001 paired")
        content = (data_dir / "logs" / "audit.log").read_text()
        entry = json.loads(content.strip())
        assert entry["detail"] == "cam-001 paired"

    def test_log_appends_multiple_events(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("EVENT_1")
        logger.log_event("EVENT_2")
        logger.log_event("EVENT_3")
        lines = (data_dir / "logs" / "audit.log").read_text().strip().splitlines()
        assert len(lines) == 3

    def test_log_defaults_empty_strings(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("TEST_EVENT")
        content = (data_dir / "logs" / "audit.log").read_text()
        entry = json.loads(content.strip())
        assert entry["user"] == ""
        assert entry["ip"] == ""
        assert entry["detail"] == ""

    def test_log_handles_write_error(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        with patch("builtins.open", side_effect=OSError("disk full")):
            # Should not raise, just log the error
            logger.log_event("TEST_EVENT")


class TestGetEvents:
    """Test reading audit events."""

    def test_get_events_empty(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        assert logger.get_events() == []

    def test_get_events_returns_list(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("EVENT_1")
        events = logger.get_events()
        assert isinstance(events, list)
        assert len(events) == 1

    def test_get_events_most_recent_first(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("FIRST")
        logger.log_event("SECOND")
        logger.log_event("THIRD")
        events = logger.get_events()
        assert events[0]["event"] == "THIRD"
        assert events[2]["event"] == "FIRST"

    def test_get_events_with_limit(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        for i in range(10):
            logger.log_event(f"EVENT_{i}")
        events = logger.get_events(limit=3)
        assert len(events) == 3
        assert events[0]["event"] == "EVENT_9"

    def test_get_events_filter_by_type(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("LOGIN_SUCCESS", user="admin")
        logger.log_event("LOGIN_FAILED", user="hacker")
        logger.log_event("LOGIN_SUCCESS", user="viewer")
        events = logger.get_events(event_type="LOGIN_SUCCESS")
        assert len(events) == 2
        assert all(e["event"] == "LOGIN_SUCCESS" for e in events)

    def test_get_events_filter_no_match(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        logger.log_event("LOGIN_SUCCESS")
        events = logger.get_events(event_type="NONEXISTENT")
        assert events == []

    def test_get_events_handles_corrupt_lines(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        log_file = data_dir / "logs" / "audit.log"
        log_file.write_text('{"event":"GOOD"}\nnot json\n{"event":"ALSO_GOOD"}\n')
        events = logger.get_events()
        assert len(events) == 2

    def test_get_events_handles_missing_file(self, data_dir):
        logger = AuditLogger(str(data_dir / "logs"))
        # Don't create the file
        assert logger.get_events() == []


class TestConcurrency:
    """Test thread-safe logging."""

    def test_concurrent_writes(self, data_dir):
        import threading

        logger = AuditLogger(str(data_dir / "logs"))
        errors = []

        def write_event(i):
            try:
                logger.log_event(f"EVENT_{i}", user=f"user{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_event, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        events = logger.get_events(limit=100)
        assert len(events) == 20

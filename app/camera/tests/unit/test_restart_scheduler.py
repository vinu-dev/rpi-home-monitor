# REQ: SWR-072; RISK: RISK-001, RISK-015; SEC: SC-001, SC-002, SC-008; TEST: TC-057
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from camera_streamer.restart_scheduler import CameraRestartScheduler


def test_camera_restart_scheduler_triggers_once_per_scheduled_minute(
    tmp_path, monkeypatch
):
    reboot = MagicMock()
    monkeypatch.setattr(
        "camera_streamer.restart_scheduler.request_reboot_async",
        reboot,
    )
    config = SimpleNamespace(
        config_dir=str(tmp_path),
        restart_schedule={
            "enabled": True,
            "days": ["mon"],
            "time": "04:15",
            "updated_at": "2026-01-01T00:00:00Z",
            "source": "camera",
        },
    )
    scheduler = CameraRestartScheduler(config)
    now = datetime(2026, 1, 5, 4, 15, tzinfo=UTC)

    assert scheduler.check_once(now=now) is True
    assert scheduler.check_once(now=now) is False
    reboot.assert_called_once_with("scheduled")


def test_camera_restart_scheduler_skips_and_records_when_blocked(tmp_path, monkeypatch):
    reboot = MagicMock()
    monkeypatch.setattr(
        "camera_streamer.restart_scheduler.request_reboot_async",
        reboot,
    )
    config = SimpleNamespace(
        config_dir=str(tmp_path),
        restart_schedule={
            "enabled": True,
            "days": ["mon"],
            "time": "04:15",
            "updated_at": "2026-01-01T00:00:00Z",
            "source": "camera",
        },
    )
    scheduler = CameraRestartScheduler(config, is_blocked=lambda: True)
    now = datetime(2026, 1, 5, 4, 15, tzinfo=UTC)

    assert scheduler.check_once(now=now) is False
    assert scheduler.check_once(now=now) is False
    reboot.assert_not_called()

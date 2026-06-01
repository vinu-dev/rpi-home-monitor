# REQ: SWR-072; RISK: RISK-001, RISK-015; SEC: SC-001, SC-002, SC-008; TEST: TC-057
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from monitor.models import Settings
from monitor.services.restart_schedule_model import (
    next_run_at,
    normalise_restart_schedule,
)
from monitor.services.restart_schedule_service import RestartScheduleService


def test_normalise_restart_schedule_filters_days_and_time():
    schedule = normalise_restart_schedule(
        {"enabled": True, "days": ["sun", "bogus", "mon"], "time": "25:99"},
        source="server",
        stamp=True,
        now_iso="2026-06-01T00:00:00Z",
    )

    assert schedule == {
        "enabled": True,
        "days": ["mon", "sun"],
        "time": "03:30",
        "updated_at": "2026-06-01T00:00:00Z",
        "source": "server",
    }


def test_next_run_at_uses_timezone_and_selected_days():
    schedule = {
        "enabled": True,
        "days": ["mon"],
        "time": "04:15",
        "updated_at": "2026-06-01T00:00:00Z",
        "source": "server",
    }
    now = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)

    assert next_run_at(schedule, "UTC", now=now).endswith("04:15:00+00:00")


def test_service_triggers_scheduled_reboot_once(tmp_path):
    settings = Settings(
        timezone="UTC",
        restart_schedule={
            "enabled": True,
            "days": ["mon"],
            "time": "04:15",
            "updated_at": "2026-06-01T00:00:00Z",
            "source": "server",
        },
    )
    store = SimpleNamespace(get_settings=MagicMock(return_value=settings))
    service = RestartScheduleService(store=store, config_dir=str(tmp_path))
    service._schedule_reboot_async = MagicMock()
    now = datetime(2026, 6, 1, 4, 15, tzinfo=UTC)

    assert service.check_once(now=now) is True
    assert service.check_once(now=now) is False
    service._schedule_reboot_async.assert_called_once_with("scheduled")

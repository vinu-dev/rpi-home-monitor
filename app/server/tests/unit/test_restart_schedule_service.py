# REQ: SWR-072; RISK: RISK-001, RISK-015; SEC: SC-001, SC-002, SC-008; TEST: TC-057
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from monitor.models import Settings
from monitor.services import privileged
from monitor.services.restart_schedule_model import (
    is_schedule_newer,
    next_run_at,
    normalise_restart_schedule,
    scheduled_minute_key,
    timezone_or_utc,
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


def test_normalise_restart_schedule_accepts_csv_days_and_sanitises_source():
    schedule = normalise_restart_schedule(
        {
            "enabled": True,
            "days": "fri, nope, mon",
            "time": "07:05",
            "source": "operator",
            "updated_at": "2026-06-01T00:00:00Z",
        },
        source="server",
        stamp=False,
    )

    assert schedule == {
        "enabled": True,
        "days": ["mon", "fri"],
        "time": "07:05",
        "updated_at": "2026-06-01T00:00:00Z",
        "source": "server",
    }


def test_normalise_restart_schedule_handles_non_mapping_payload():
    schedule = normalise_restart_schedule(["bad"], source="camera", stamp=False)

    assert schedule == {
        "enabled": False,
        "days": ["sun"],
        "time": "03:30",
        "updated_at": "",
        "source": "camera",
    }


def test_schedule_timestamp_comparison():
    older = {"updated_at": "2026-06-01T00:00:00Z"}
    newer = {"updated_at": "2026-06-01T00:01:00Z"}

    assert is_schedule_newer(newer, older) is True
    assert is_schedule_newer(older, newer) is False
    assert is_schedule_newer({}, newer) is False


def test_timezone_or_utc_falls_back_for_unknown_zone():
    assert str(timezone_or_utc("Not/AZone")) == "UTC"


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


def test_next_run_at_skips_disabled_schedule():
    assert next_run_at({"enabled": False}, "UTC") == ""


def test_scheduled_minute_key_requires_enabled_day_and_time():
    schedule = {
        "enabled": True,
        "days": ["tue"],
        "time": "04:15",
        "updated_at": "",
        "source": "server",
    }
    now = datetime(2026, 6, 1, 4, 15, tzinfo=UTC)

    assert scheduled_minute_key(now, schedule) == ""
    schedule["days"] = ["mon"]
    schedule["time"] = "04:16"
    assert scheduled_minute_key(now, schedule) == ""
    schedule["time"] = "04:15"
    assert scheduled_minute_key(now, schedule) == "2026-06-01T04:15"


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


def test_service_status_adds_next_run(tmp_path):
    settings = Settings(
        timezone="UTC",
        restart_schedule={
            "enabled": True,
            "days": ["mon"],
            "time": "04:15",
            "updated_at": "",
            "source": "server",
        },
    )
    store = SimpleNamespace(get_settings=MagicMock(return_value=settings))
    service = RestartScheduleService(store=store, config_dir=str(tmp_path))

    status = service.server_schedule_status()

    assert status["enabled"] is True
    assert status["next_run_at"]


def test_update_server_schedule_persists_and_audits(tmp_path):
    settings = Settings(timezone="UTC")
    store = SimpleNamespace(
        get_settings=MagicMock(return_value=settings),
        save_settings=MagicMock(),
    )
    audit = SimpleNamespace(log_event=MagicMock())
    service = RestartScheduleService(
        store=store,
        config_dir=str(tmp_path),
        audit=audit,
    )

    result, error, status = service.update_server_schedule(
        {"enabled": True, "days": ["wed"], "time": "02:00", "source": "camera"},
        requesting_user="admin",
        requesting_ip="192.0.2.10",
    )

    assert error == ""
    assert status == 200
    assert result["source"] == "server"
    assert result["enabled"] is True
    store.save_settings.assert_called_once_with(settings)
    audit.log_event.assert_called_once()


def test_request_server_reboot_schedules_async_worker(tmp_path):
    store = SimpleNamespace(get_settings=MagicMock(return_value=Settings()))
    service = RestartScheduleService(store=store, config_dir=str(tmp_path))
    service._schedule_reboot_async = MagicMock()

    message, status = service.request_server_reboot(reason="manual")

    assert message == "Server reboot requested"
    assert status == 202
    service._schedule_reboot_async.assert_called_once_with("manual")


def test_service_skips_schedule_when_blocked_and_marks_run(tmp_path):
    settings = Settings(
        timezone="UTC",
        restart_schedule={
            "enabled": True,
            "days": ["mon"],
            "time": "04:15",
            "updated_at": "",
            "source": "server",
        },
    )
    store = SimpleNamespace(get_settings=MagicMock(return_value=settings))
    service = RestartScheduleService(
        store=store,
        config_dir=str(tmp_path),
        is_blocked=lambda: True,
    )
    service._schedule_reboot_async = MagicMock()
    now = datetime(2026, 6, 1, 4, 15, tzinfo=UTC)

    assert service.check_once(now=now) is False
    service._schedule_reboot_async.assert_not_called()
    assert service._has_run("server", "2026-06-01T04:15") is True


class _ImmediateThread:
    def __init__(self, target, *args, **kwargs):
        self._target = target

    def start(self):
        self._target()


def test_schedule_reboot_async_uses_systemctl_without_helper(tmp_path):
    store = SimpleNamespace(get_settings=MagicMock(return_value=Settings()))
    service = RestartScheduleService(
        store=store,
        config_dir=str(tmp_path),
        reboot_delay_seconds=0,
    )

    with (
        patch(
            "monitor.services.restart_schedule_service.threading.Thread",
            _ImmediateThread,
        ),
        patch("monitor.services.restart_schedule_service.time.sleep"),
        patch(
            "monitor.services.restart_schedule_service.privileged.should_use_helper",
            return_value=False,
        ),
        patch("monitor.services.restart_schedule_service.subprocess.run") as mock_run,
    ):
        service._schedule_reboot_async("manual")

    mock_run.assert_called_once_with(["systemctl", "reboot"], check=False, timeout=15)


def test_schedule_reboot_async_handles_privileged_helper_errors(tmp_path):
    store = SimpleNamespace(get_settings=MagicMock(return_value=Settings()))
    service = RestartScheduleService(
        store=store,
        config_dir=str(tmp_path),
        reboot_delay_seconds=0,
    )

    with (
        patch(
            "monitor.services.restart_schedule_service.threading.Thread",
            _ImmediateThread,
        ),
        patch("monitor.services.restart_schedule_service.time.sleep"),
        patch(
            "monitor.services.restart_schedule_service.privileged.should_use_helper",
            return_value=True,
        ),
        patch(
            "monitor.services.restart_schedule_service.privileged.request",
            side_effect=privileged.PrivilegedHelperError("boom"),
        ),
    ):
        service._schedule_reboot_async("manual")


def test_state_ignores_invalid_json_and_non_object(tmp_path):
    store = SimpleNamespace(get_settings=MagicMock(return_value=Settings()))
    service = RestartScheduleService(store=store, config_dir=str(tmp_path))

    service._state_path.write_text("{bad", encoding="utf-8")
    assert service._state() == {}

    service._state_path.write_text("[]", encoding="utf-8")
    assert service._state() == {}


def test_audit_failures_do_not_escape(tmp_path):
    store = SimpleNamespace(get_settings=MagicMock(return_value=Settings()))
    audit = SimpleNamespace(log_event=MagicMock(side_effect=RuntimeError("boom")))
    service = RestartScheduleService(
        store=store,
        config_dir=str(tmp_path),
        audit=audit,
    )

    service._log_audit("EVENT", "admin", "192.0.2.10", "detail")

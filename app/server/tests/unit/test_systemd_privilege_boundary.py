# REQ: SWR-027, SWR-028, SWR-063; RISK: RISK-013, RISK-018; SEC: SC-013, SC-019; TEST: TC-024, TC-025, TC-044
"""Static checks for the monitor root privilege boundary."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
MONITOR_UNIT = REPO_ROOT / "app" / "server" / "config" / "monitor.service"
HELPER_UNIT = (
    REPO_ROOT / "app" / "server" / "config" / "monitor-privileged-helper.service"
)


def _directives(path: Path) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        directives.setdefault(key, []).append(value)
    return directives


def test_flask_monitor_unit_runs_unprivileged_and_depends_on_helper():
    data = _directives(MONITOR_UNIT)

    assert data["User"] == ["monitor"]
    assert data["Group"] == ["monitor"]
    assert "monitor-privileged-helper.service" in data["Requires"][0]
    assert "monitor-privileged-helper.service" in data["After"][0]
    assert data["NoNewPrivileges"] == ["true"]
    assert data["ProtectHome"] == ["true"]


def test_helper_unit_is_the_only_root_service_in_boundary():
    data = _directives(HELPER_UNIT)

    assert data["User"] == ["root"]
    assert data["Group"] == ["monitor"]
    assert data["RuntimeDirectory"] == ["monitor"]
    assert data["RuntimeDirectoryMode"] == ["0750"]
    assert data["ExecStart"] == [
        "/usr/bin/python3 -m monitor.services.privileged_helper"
    ]
    assert "CAP_SYS_ADMIN" in data["CapabilityBoundingSet"][0]


def test_yocoto_recipe_installs_helper_unit():
    recipe = (
        REPO_ROOT
        / "meta-home-monitor"
        / "recipes-monitor"
        / "monitor-server"
        / "monitor-server_1.0.bb"
    ).read_text(encoding="utf-8")
    services = next(
        line for line in recipe.splitlines() if line.startswith("SYSTEMD_SERVICE:${PN}")
    )

    assert "file://config/monitor-privileged-helper.service" in recipe
    assert "monitor-privileged-helper.service" in services
    assert "monitor.service" in services
    assert services.index("monitor-privileged-helper.service") < services.index(
        "monitor.service"
    )
    assert "${systemd_system_unitdir}/monitor-privileged-helper.service" in recipe

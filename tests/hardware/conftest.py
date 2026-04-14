"""Shared bridge-phase fixtures for hardware validation."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "hardware"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test reports on the node for post-test artifact capture."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@dataclass(frozen=True)
class HardwareTarget:
    role: str
    host: str
    ssh_user: str = "root"
    ssh_port: int = 22

    @property
    def target(self) -> str:
        return f"{self.ssh_user}@{self.host}"

    def ssh(
        self, command: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if shutil.which("ssh") is None:
            pytest.skip("ssh client not installed on this runner")
        return subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-p",
                str(self.ssh_port),
                self.target,
                command,
            ],
            check=check,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )

    def read_journal(self, services: Iterable[str], *, lines: int = 200) -> str:
        service_args = " ".join(f"-u {service}" for service in services)
        result = self.ssh(
            f"journalctl {service_args} -n {lines} --no-pager", check=False
        )
        return result.stdout or result.stderr


def _env_target(role: str) -> HardwareTarget | None:
    host = os.environ.get(f"HIL_{role}")
    if not host:
        return None
    return HardwareTarget(
        role=role.lower(),
        host=host,
        ssh_user=os.environ.get(f"HIL_{role}_SSH_USER", "root"),
        ssh_port=int(os.environ.get(f"HIL_{role}_SSH_PORT", "22")),
    )


def _artifact_root() -> Path:
    root = Path(os.environ.get("HIL_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR))
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="session")
def hardware_artifact_root() -> Path:
    """Directory where bridge-phase hardware logs are written."""
    return _artifact_root()


@pytest.fixture
def hardware_artifact_dir(request, hardware_artifact_root: Path) -> Path:
    """Per-test artifact directory."""
    node_name = (
        request.node.nodeid.replace("::", "__").replace("/", "_").replace("\\", "_")
    )
    path = hardware_artifact_root / node_name
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def server_target() -> HardwareTarget:
    target = _env_target("SERVER")
    if target is None:
        pytest.skip("missing hardware environment variable: HIL_SERVER")
    return target


@pytest.fixture
def camera_target() -> HardwareTarget:
    target = _env_target("CAMERA")
    if target is None:
        pytest.skip("missing hardware environment variable: HIL_CAMERA")
    return target


@pytest.fixture
def wait_for_http():
    """Poll an HTTP(S) endpoint until it responds."""

    def _wait(url: str, *, timeout: int = 60, insecure: bool = True) -> None:
        import ssl
        import urllib.request

        deadline = time.time() + timeout
        context = ssl._create_unverified_context() if insecure else None
        while time.time() < deadline:
            try:
                urllib.request.urlopen(url, context=context, timeout=5)
                return
            except Exception:
                time.sleep(2)
        pytest.fail(f"timed out waiting for {url}")

    return _wait


@pytest.fixture(autouse=True)
def collect_hardware_logs(request, hardware_artifact_dir: Path):
    """Capture journald excerpts for live hardware tests when they fail."""
    yield

    report = getattr(request.node, "rep_call", None)
    if report is None or report.passed or report.skipped:
        return

    target_specs = {
        "server": (("monitor", "mediamtx"), _env_target("SERVER")),
        "camera": (("camera-streamer",), _env_target("CAMERA")),
    }
    for name, (services, target) in target_specs.items():
        if target is None:
            continue
        try:
            log_output = target.read_journal(services)
        except Exception as exc:  # pragma: no cover - failure capture path
            log_output = f"failed to capture logs from {name}: {exc}\n"
        (hardware_artifact_dir / f"{name}-journal.txt").write_text(
            log_output,
            encoding="utf-8",
        )

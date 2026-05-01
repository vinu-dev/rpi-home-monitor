"""Integration tests for ensure-camera-overlay.sh.

The script reconciles ``/boot/config.txt`` to a canonical "managed block"
that selects the camera sensor (firmware auto-detect by default, explicit
override via ``/data/config/camera-sensor``).

These tests run the real shell script in self-test mode against fixture
config.txt files. Self-test mode never touches a real boot partition;
``$BOOT_CONFIG`` is the fixture path and the mount/sync calls are stubbed
inside the script. This lets us drive the script identically to how it
runs on the camera, without root or hardware.

Invariants under test:

1. Script is idempotent against any input — a second run leaves the file
   byte-identical to the first run's output.
2. Clean auto-detect is the default — produces a single
   ``camera_auto_detect=1`` line and no explicit dtoverlay.
3. The "stale duplicate" state observed live on a lab camera (leading-whitespace
   ``camera_auto_detect=0`` + ``dtoverlay=ov5647`` from RPI_EXTRA_CONFIG,
   plus bare duplicates appended by the original script) heals in a
   single run.
4. Already-correct state is a no-op (file unchanged on disk; the script
   detects the cmp-equal case before remounting).
5. ``/data/config/camera-sensor`` override pins the requested sensor
   when the file contains a recognised name; ignored otherwise.
6. Other dtoverlay lines (vc4-fkms-v3d, act-led, ...) are preserved.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "ensure-camera-overlay.sh"
)


def _run(
    config_path: Path, override_path: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke the script in self-test mode against the given fixture."""
    env = os.environ.copy()
    env["HM_OVERRIDE_FILE"] = str(override_path) if override_path else "/dev/null"
    return subprocess.run(
        ["sh", str(SCRIPT_PATH), "--self-test", str(config_path)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def _write(path: Path, content: str) -> None:
    """Write fixture content with LF line endings on every platform.

    Default ``write_text`` translates ``\\n`` to ``os.linesep`` on Windows,
    which would inject ``\\r\\n`` into the fixture and make awk see literal
    CR characters. The script ships LF-only because the camera runs Linux,
    so the test must mirror that.
    """
    text = textwrap.dedent(content).lstrip("\n")
    path.write_bytes(text.encode("utf-8"))


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


# Fixture A — clean image just out of bitbake. The Yocto-baked
# RPI_EXTRA_CONFIG already produced a leading-whitespace block.
FIXTURE_CLEAN_YOCTO_BAKE = """
    # Some unrelated boot config
    enable_uart=1
    dtoverlay=vc4-fkms-v3d

     camera_auto_detect=0
     dtoverlay=ov5647

    # have a properly sized image
    disable_overscan=1
"""

# Fixture B — lab-camera stale state: leading-ws block from the OLD Yocto
# bake (before this PR), plus bare duplicates appended by the OLD
# ensure-camera-overlay.sh when its grep failed to match the leading-ws
# variant.
FIXTURE_STALE_DUPLICATES = """
    enable_uart=1
    dtoverlay=vc4-fkms-v3d

     camera_auto_detect=0
     dtoverlay=ov5647

    # have a properly sized image
    disable_overscan=1
    dtparam=audio=on

    # Camera sensor (added by ensure-camera-overlay)
    camera_auto_detect=0
    dtoverlay=ov5647
"""

# Fixture C — already-correct state (post-fix file produced by this script
# on a previous boot). Running the script must not change the file.
FIXTURE_ALREADY_CORRECT = """
    enable_uart=1
    dtoverlay=vc4-fkms-v3d
    disable_overscan=1
    dtparam=audio=on

    # Camera sensor (managed by ensure-camera-overlay)
    camera_auto_detect=1
"""

# Fixture D — hand-patched state from this session's manual validation:
# the auto-detect line is bare (no leading ws) and the original
# dtoverlay=ov5647 lines have been commented out with a "disabled" marker.
FIXTURE_HAND_PATCHED = """
    enable_uart=1
    dtoverlay=vc4-fkms-v3d

    # camera_auto_detect=0  # disabled 2026-04-25T09:46Z for OV5647 auto-detect test
    # dtoverlay=ov5647  # disabled 2026-04-25T09:46Z for OV5647 auto-detect test

    disable_overscan=1
    dtparam=audio=on

    # Camera sensor (added by ensure-camera-overlay)
    camera_auto_detect=1
    #dtoverlay=ov5647  # disabled 2026-04-25T09:46Z for OV5647 auto-detect test
"""


class TestAutoDetect:
    def test_clean_yocto_bake_collapses_to_canonical_block(
        self, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_CLEAN_YOCTO_BAKE)
        _run(cfg)
        out = _read(cfg)
        # No active OV5647 or auto_detect=0 line anywhere.
        assert not any(
            line.lstrip().startswith("dtoverlay=ov5647") for line in out.splitlines()
        ), out
        assert not any(
            line.lstrip().startswith("camera_auto_detect=0")
            for line in out.splitlines()
        ), out
        # Exactly one auto_detect=1.
        assert (
            sum(
                1
                for line in out.splitlines()
                if line.lstrip().startswith("camera_auto_detect=1")
            )
            == 1
        ), out
        # Unrelated overlays preserved.
        assert "dtoverlay=vc4-fkms-v3d" in out
        assert "enable_uart=1" in out
        assert "disable_overscan=1" in out

    def test_stale_duplicates_heal_in_one_run(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_STALE_DUPLICATES)
        _run(cfg)
        out = _read(cfg)
        assert " dtoverlay=ov5647" not in out
        assert "dtoverlay=ov5647" not in out
        assert " camera_auto_detect=0" not in out
        # Old "added by" header is removed; new "managed by" header is added.
        assert "added by ensure-camera-overlay" not in out
        assert "managed by ensure-camera-overlay" in out
        assert (
            sum(
                1 for line in out.splitlines() if line.strip() == "camera_auto_detect=1"
            )
            == 1
        )

    def test_hand_patched_state_collapses_cleanly(self, tmp_path: Path) -> None:
        """The hand-patched state from manual validation should normalise
        to the canonical managed block — disabled-marker comments dropped,
        single auto_detect=1, no orphan disabled-OV5647 lines."""
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_HAND_PATCHED)
        _run(cfg)
        out = _read(cfg)
        assert "disabled 2026-04-25" not in out
        assert "managed by ensure-camera-overlay" in out
        assert (
            sum(
                1 for line in out.splitlines() if line.strip() == "camera_auto_detect=1"
            )
            == 1
        )

    def test_idempotent_second_run_is_byte_identical(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_STALE_DUPLICATES)
        _run(cfg)
        first = _read(cfg)
        _run(cfg)
        second = _read(cfg)
        assert first == second

    def test_already_correct_state_is_noop(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_ALREADY_CORRECT)
        before = _read(cfg)
        before_mtime = cfg.stat().st_mtime_ns
        result = _run(cfg)
        after = _read(cfg)
        assert before == after
        # Stdout reports the no-change branch.
        assert "no changes" in result.stdout
        # mtime unchanged confirms cmp-equal short-circuit fired.
        assert cfg.stat().st_mtime_ns == before_mtime


class TestSensorOverride:
    @pytest.mark.parametrize("sensor", ["ov5647", "imx219", "imx477", "imx708"])
    def test_override_pins_explicit_sensor(self, tmp_path: Path, sensor: str) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_CLEAN_YOCTO_BAKE)
        override = tmp_path / "camera-sensor"
        override.write_text(sensor + "\n")
        _run(cfg, override_path=override)
        out = _read(cfg)
        # Override turns auto_detect off and pins the named overlay.
        assert "camera_auto_detect=0" in out
        assert "camera_auto_detect=1" not in out
        assert f"dtoverlay={sensor}" in out
        # No competing sensor overlays.
        for other in ("ov5647", "imx219", "imx477", "imx708"):
            if other == sensor:
                continue
            assert f"dtoverlay={other}" not in out

    def test_override_handles_whitespace_and_case(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_CLEAN_YOCTO_BAKE)
        override = tmp_path / "camera-sensor"
        override.write_text("  IMX219  \n")
        _run(cfg, override_path=override)
        out = _read(cfg)
        assert "dtoverlay=imx219" in out
        assert "camera_auto_detect=0" in out

    def test_unknown_override_falls_back_to_auto_detect(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_CLEAN_YOCTO_BAKE)
        override = tmp_path / "camera-sensor"
        override.write_text("totally-bogus-sensor\n")
        result = _run(cfg, override_path=override)
        out = _read(cfg)
        assert "camera_auto_detect=1" in out
        assert "dtoverlay=ov5647" not in out
        # Warning emitted on stderr.
        assert "ignoring unrecognised override" in result.stderr

    def test_empty_override_falls_back_to_auto_detect(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_CLEAN_YOCTO_BAKE)
        override = tmp_path / "camera-sensor"
        override.write_text("\n")
        _run(cfg, override_path=override)
        out = _read(cfg)
        assert "camera_auto_detect=1" in out
        assert "dtoverlay=ov5647" not in out

    def test_override_idempotent(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.txt"
        _write(cfg, FIXTURE_STALE_DUPLICATES)
        override = tmp_path / "camera-sensor"
        override.write_text("imx219\n")
        _run(cfg, override_path=override)
        first = _read(cfg)
        _run(cfg, override_path=override)
        second = _read(cfg)
        assert first == second

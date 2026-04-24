"""Unit tests for LoopRecorder (ADR-0017)."""

import os
import time
from unittest.mock import MagicMock

from monitor.services.loop_recorder import LoopRecorder


def _mkseg(base, cam, name, age_seconds):
    """Create a fake .mp4 segment with a specific mtime."""
    d = base / cam
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"\x00" * 100)
    t = time.time() - age_seconds
    os.utime(p, (t, t))
    return p


class TestLoopRecorderWatermark:
    def test_no_deletion_above_watermark(self, tmp_path, monkeypatch):
        _mkseg(tmp_path, "cam1", "old.mp4", 4000)
        _mkseg(tmp_path, "cam1", "new.mp4", 3000)

        lr = LoopRecorder(tmp_path, audit=MagicMock(), low_watermark=10)
        # Simulate 50% free (above watermark).
        monkeypatch.setattr(lr, "_free_percent", lambda: 50.0)

        assert lr.tick() == 0
        assert (tmp_path / "cam1" / "old.mp4").exists()
        assert (tmp_path / "cam1" / "new.mp4").exists()

    def test_deletes_oldest_when_below_watermark(self, tmp_path):
        old = _mkseg(tmp_path, "cam1", "old.mp4", 5000)
        new = _mkseg(tmp_path, "cam1", "new.mp4", 4000)

        lr = LoopRecorder(tmp_path, audit=MagicMock(), low_watermark=10, hysteresis=5)

        # Stay below watermark for the first two checks (entry + one loop
        # iter to delete the oldest), then jump above target.
        seq = iter([5.0, 5.0, 20.0, 20.0, 20.0])
        lr._free_percent = lambda: next(seq)

        deleted = lr.tick()
        assert deleted == 1
        assert not old.exists()
        assert new.exists()

    def test_hysteresis_prevents_flap(self, tmp_path):
        """Once free_percent >= low + hysteresis we stop deleting."""
        for i in range(5):
            _mkseg(tmp_path, "cam1", f"s{i}.mp4", 10000 - i)

        lr = LoopRecorder(tmp_path, audit=MagicMock(), low_watermark=10, hysteresis=5)

        # Free: entry below, first loop-iter below, then above target.
        seq = iter([5.0, 5.0, 16.0, 16.0, 16.0, 16.0])
        lr._free_percent = lambda: next(seq)

        deleted = lr.tick()
        assert deleted == 1  # hysteresis stopped further deletion

    def test_live_segments_never_deleted(self, tmp_path):
        old_live = _mkseg(tmp_path, "cam1", "live.mp4", 0)  # brand new
        truly_old = _mkseg(tmp_path, "cam1", "stale.mp4", 10000)

        lr = LoopRecorder(tmp_path, audit=MagicMock(), low_watermark=10, hysteresis=5)
        # Keep free low the entire run so we would normally keep deleting.
        lr._free_percent = lambda: 1.0

        deleted = lr.tick()
        # Live file protected by mtime (<10 min old); old one gone.
        assert old_live.exists()
        assert not truly_old.exists()
        assert deleted == 1

    def test_live_segments_callback_protects(self, tmp_path):
        """Scheduler-provided live set protects a file even if mtime is old."""
        _mkseg(tmp_path, "cam1", "a.mp4", 10000)
        live = _mkseg(tmp_path, "cam1", "b.mp4", 10000)

        lr = LoopRecorder(
            tmp_path,
            audit=MagicMock(),
            low_watermark=10,
            hysteresis=5,
            live_segments_getter=lambda: {str(live)},
        )
        lr._free_percent = lambda: 1.0
        lr.tick()
        assert live.exists()

    def test_audit_event_emitted(self, tmp_path):
        _mkseg(tmp_path, "cam1", "old.mp4", 5000)
        audit = MagicMock()
        lr = LoopRecorder(tmp_path, audit=audit, low_watermark=10, hysteresis=5)
        seq = iter([5.0, 5.0, 20.0, 20.0])
        lr._free_percent = lambda: next(seq)
        lr.tick()
        audit.log_event.assert_called_once()
        args, kwargs = audit.log_event.call_args
        assert args[0] == "RECORDING_ROTATED"


class TestLoopRecorderEdgeCases:
    def test_missing_base_dir_does_not_raise(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        lr = LoopRecorder(missing, audit=MagicMock())
        assert lr.tick() == 0

    def test_no_segments_returns_zero(self, tmp_path):
        lr = LoopRecorder(tmp_path, audit=MagicMock())
        lr._free_percent = lambda: 1.0
        assert lr.tick() == 0


class TestLoopRecorderSetBaseDir:
    def test_set_base_dir_redirects_cleanup(self, tmp_path):
        """set_base_dir must redirect both free-space checks and segment scans.

        Regression: when USB storage is selected the recordings directory
        changes from /data/recordings to /mnt/recordings/home-monitor-recordings.
        Before the fix, LoopRecorder kept watching the original internal path
        (nearly empty → free_pct always ≥ low watermark → tick() → 0) so the
        USB drive filled to 100% and was never pruned.
        """
        internal = tmp_path / "internal"
        internal.mkdir()
        usb = tmp_path / "usb"
        usb.mkdir()

        # Old segment sits on USB; internal is empty.
        old_seg = _mkseg(usb, "cam1", "old.mp4", 5000)

        lr = LoopRecorder(internal, audit=MagicMock(), low_watermark=10, hysteresis=5)

        # Before redirect: free_percent targets internal (plenty of space) →
        # tick does nothing even though USB is full.
        lr._free_percent = lambda: 80.0
        assert lr.tick() == 0
        assert old_seg.exists()

        # Simulate USB mount: redirect the recorder to the USB path.
        lr.set_base_dir(usb)
        assert lr._base_dir == usb

        # Now free_percent is patched to reflect the full USB; tick prunes.
        seq = iter([2.0, 2.0, 20.0, 20.0])
        lr._free_percent = lambda: next(seq)
        deleted = lr.tick()
        assert deleted == 1
        assert not old_seg.exists()

    def test_set_base_dir_accepts_string(self, tmp_path):
        lr = LoopRecorder(tmp_path, audit=MagicMock())
        lr.set_base_dir(str(tmp_path / "new"))
        from pathlib import Path

        assert lr._base_dir == Path(tmp_path / "new")

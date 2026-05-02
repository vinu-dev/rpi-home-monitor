# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
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
        """Each deletion emits RECORDING_ROTATED. After #140 the same
        tick may also emit STORAGE_LOW and RETENTION_RISK on threshold
        crossings; verify the per-deletion event still fires by code,
        not by total call count.
        """
        _mkseg(tmp_path, "cam1", "old.mp4", 5000)
        audit = MagicMock()
        lr = LoopRecorder(tmp_path, audit=audit, low_watermark=10, hysteresis=5)
        seq = iter([5.0, 5.0, 20.0, 20.0])
        lr._free_percent = lambda: next(seq)
        lr.tick()
        codes = [call.args[0] for call in audit.log_event.call_args_list]
        assert codes.count("RECORDING_ROTATED") == 1


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


def _audit_codes(audit_mock):
    """Return the list of event codes emitted via audit.log_event."""
    return [call.args[0] for call in audit_mock.log_event.call_args_list]


class TestStorageHealthAlerts:
    """Edge-detected STORAGE_LOW + RETENTION_RISK audits (#140).

    The loop recorder already emits ``RECORDING_ROTATED`` per-deletion;
    these new codes are *health* signals — emitted on threshold-crossings
    only, not per metrics tick. The alert center (#208) consumes them
    via the catalogue in ``ALERT_AUDIT_EVENTS``.
    """

    def _new_recorder(self, tmp_path, audit):
        # Default low/hysteresis = 10/5 → STORAGE_LOW threshold = 15%.
        return LoopRecorder(
            tmp_path,
            audit=audit,
            low_watermark=10,
            hysteresis=5,
            storage_low_headroom=5,
        )

    def test_storage_low_emits_once_on_threshold_cross(self, tmp_path):
        audit = MagicMock()
        lr = self._new_recorder(tmp_path, audit)

        # Below the storage-low threshold (15%) — emit on first tick.
        lr._free_percent = lambda: 12.0
        lr.tick()
        assert "STORAGE_LOW" in _audit_codes(audit)
        first_call_count = audit.log_event.call_count

        # Stay below — second tick does NOT re-emit (state-edge gate).
        lr.tick()
        assert audit.log_event.call_count == first_call_count

    def test_storage_low_recovery_rearms_alert(self, tmp_path):
        """Once disk recovers above threshold, re-crossing back below
        is a fresh transition and emits another STORAGE_LOW."""
        audit = MagicMock()
        lr = self._new_recorder(tmp_path, audit)

        # Down → emit
        lr._free_percent = lambda: 12.0
        lr.tick()
        # Up — silent (no recovery audit per ADR-0024)
        lr._free_percent = lambda: 50.0
        lr.tick()
        # Down again — emit again
        lr._free_percent = lambda: 12.0
        lr.tick()

        codes = _audit_codes(audit)
        assert codes.count("STORAGE_LOW") == 2

    def test_storage_low_does_not_fire_above_threshold(self, tmp_path):
        audit = MagicMock()
        lr = self._new_recorder(tmp_path, audit)
        lr._free_percent = lambda: 50.0
        lr.tick()
        assert "STORAGE_LOW" not in _audit_codes(audit)

    def test_retention_risk_emits_when_actively_deleting(self, tmp_path):
        old = _mkseg(tmp_path, "cam1", "old.mp4", 10000)
        audit = MagicMock()
        lr = self._new_recorder(tmp_path, audit)

        # Stay well below the cleanup watermark for the entry + one
        # loop-iter, then jump above target.
        seq = iter([5.0, 5.0, 5.0, 20.0, 20.0, 20.0, 20.0])
        lr._free_percent = lambda: next(seq)

        lr.tick()
        codes = _audit_codes(audit)
        assert "RETENTION_RISK" in codes
        # Storage-low always fires alongside (since it's also below
        # 15% on entry) — both are independently useful signals.
        assert "STORAGE_LOW" in codes
        # Recording rotation is still per-deletion (not edge-detected).
        assert "RECORDING_ROTATED" in codes
        assert not old.exists()

    def test_retention_risk_does_not_re_emit_until_recovery(self, tmp_path):
        """Two consecutive ticks where deletes happen — only one
        RETENTION_RISK alert. The flag clears only when free recovers
        above the cleanup watermark."""
        for i in range(6):
            _mkseg(tmp_path, "cam1", f"s{i}.mp4", 10000 - i)

        audit = MagicMock()
        lr = self._new_recorder(tmp_path, audit)

        # Use a generator that always returns "well below cleanup
        # watermark" so each tick deletes one segment via the
        # candidate loop guard. The post-delete free check inside
        # tick() reads the same value, but the loop terminates after
        # the first deletion because we hit max_delete-style limits
        # (we'll just exit the candidate loop when the target check
        # never trips; the for-loop bound is the candidates list).
        # Strategy: sequence where the post-delete check eventually
        # exceeds target so the loop stops. 12 values is plenty.
        def low_then_recover():
            # entry low, mid-loop low, post-delete recovers
            yield 5.0
            yield 5.0
            yield 5.0
            yield 20.0
            yield 20.0

        gen1 = low_then_recover()
        lr._free_percent = lambda: next(gen1)
        lr.tick()

        gen2 = low_then_recover()
        lr._free_percent = lambda: next(gen2)
        lr.tick()

        codes = _audit_codes(audit)
        # Only one RETENTION_RISK across both ticks
        assert codes.count("RETENTION_RISK") == 1

    def test_retention_risk_clears_on_recovery(self, tmp_path):
        """After full recovery (above watermark) the flag re-arms;
        a subsequent fresh deletion event emits a new RETENTION_RISK."""
        for i in range(6):
            _mkseg(tmp_path, "cam1", f"s{i}.mp4", 10000 - i)

        audit = MagicMock()
        lr = self._new_recorder(tmp_path, audit)

        def low_then_recover():
            yield 5.0
            yield 5.0
            yield 5.0
            yield 20.0
            yield 20.0

        # First crossing — emits
        gen1 = low_then_recover()
        lr._free_percent = lambda: next(gen1)
        lr.tick()

        # Now well above watermark — silent recovery; flag clears
        lr._free_percent = lambda: 50.0
        lr.tick()

        # Crossing again — fresh emission
        gen3 = low_then_recover()
        lr._free_percent = lambda: next(gen3)
        lr.tick()

        codes = _audit_codes(audit)
        assert codes.count("RETENTION_RISK") == 2

    def test_retention_risk_not_emitted_when_only_storage_low(self, tmp_path):
        """Below storage-low headroom but above cleanup watermark =
        STORAGE_LOW only. RETENTION_RISK requires actual deletes."""
        audit = MagicMock()
        lr = self._new_recorder(tmp_path, audit)
        # 12% free: below storage-low threshold (15%) but above
        # cleanup watermark (10%) — so no deletes.
        lr._free_percent = lambda: 12.0
        lr.tick()
        codes = _audit_codes(audit)
        assert "STORAGE_LOW" in codes
        assert "RETENTION_RISK" not in codes
        assert "RECORDING_ROTATED" not in codes

    def test_no_audit_emission_without_audit_logger(self, tmp_path):
        """LoopRecorder accepts audit=None; the new helper must
        no-op silently rather than crashing the cleanup loop."""
        lr = self._new_recorder(tmp_path, audit=None)
        lr._free_percent = lambda: 5.0
        # Just must not raise.
        lr.tick()

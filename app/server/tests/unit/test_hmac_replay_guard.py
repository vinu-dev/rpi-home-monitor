# REQ: SWR-003, SWR-004; RISK: RISK-002, RISK-005; SEC: SC-002; TEST: TC-012
"""Tests for the persistent camera HMAC replay guard."""

from monitor.services.hmac_replay_guard import HmacReplayGuard


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_replay_state_persists_across_guard_instances(tmp_path):
    clock = FakeClock()
    state_path = tmp_path / "camera_hmac_replay.json"
    first = HmacReplayGuard(state_path, ttl_seconds=30, clock=clock)

    assert first.record_and_check("cam-001", "1000", "sig-a") is False

    restarted = HmacReplayGuard(state_path, ttl_seconds=30, clock=clock)

    assert restarted.record_and_check("cam-001", "1000", "sig-a") is True


def test_different_camera_or_signature_gets_separate_budget(tmp_path):
    clock = FakeClock()
    guard = HmacReplayGuard(
        tmp_path / "camera_hmac_replay.json",
        ttl_seconds=30,
        clock=clock,
    )

    assert guard.record_and_check("cam-001", "1000", "sig-a") is False
    assert guard.record_and_check("cam-002", "1000", "sig-a") is False
    assert guard.record_and_check("cam-001", "1000", "sig-b") is False


def test_expired_replay_entries_are_pruned(tmp_path):
    clock = FakeClock()
    guard = HmacReplayGuard(
        tmp_path / "camera_hmac_replay.json",
        ttl_seconds=30,
        clock=clock,
    )

    assert guard.record_and_check("cam-001", "1000", "sig-a") is False
    clock.advance(31)

    assert guard.record_and_check("cam-001", "1000", "sig-a") is False

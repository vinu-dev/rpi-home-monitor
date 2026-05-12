# REQ: SWR-001; RISK: RISK-002; SEC: SC-001; TEST: TC-004
"""Tests for the persistent server login rate limiter."""

from monitor.services.login_rate_limiter import LoginRateLimiter


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_persists_block_state_across_limiter_instances(tmp_path):
    clock = FakeClock()
    state_path = tmp_path / "login_rate_limits.json"
    first = LoginRateLimiter(
        state_path,
        window_seconds=60,
        warn_after=5,
        block_after=10,
        clock=clock,
    )

    for _ in range(10):
        first.record("192.0.2.10")

    restarted = LoginRateLimiter(
        state_path,
        window_seconds=60,
        warn_after=5,
        block_after=10,
        clock=clock,
    )

    assert restarted.check("192.0.2.10") == (False, False)


def test_multiple_instances_share_one_budget(tmp_path):
    clock = FakeClock()
    state_path = tmp_path / "login_rate_limits.json"
    first = LoginRateLimiter(
        state_path,
        window_seconds=60,
        warn_after=5,
        block_after=10,
        clock=clock,
    )
    second = LoginRateLimiter(
        state_path,
        window_seconds=60,
        warn_after=5,
        block_after=10,
        clock=clock,
    )

    for _ in range(5):
        first.record("192.0.2.11")
    for _ in range(5):
        second.record("192.0.2.11")

    assert first.check("192.0.2.11") == (False, False)


def test_expired_attempts_are_pruned(tmp_path):
    clock = FakeClock()
    limiter = LoginRateLimiter(
        tmp_path / "login_rate_limits.json",
        window_seconds=60,
        warn_after=5,
        block_after=10,
        clock=clock,
    )

    for _ in range(10):
        limiter.record("192.0.2.12")

    clock.advance(61)

    assert limiter.check("192.0.2.12") == (True, False)

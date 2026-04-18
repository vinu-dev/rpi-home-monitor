"""Property-based tests for authentication primitives (monitor.auth).

Hypothesis drives the inputs so that invariants hold across the entire
input space, not just hand-picked examples.

Properties under test:
  - hash_password / check_password: correct-password round-trip always succeeds.
  - check_password: wrong password always fails.
  - _get_lockout_duration: monotonically non-decreasing with failure count.
  - _check_rate_limit: hard block fires exactly at RATE_LIMIT_BLOCK attempts.
  - CSRF token: always 64-char lowercase hex, unique across calls.
"""

import time
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from monitor.auth import (
    LOCKOUT_THRESHOLDS,
    RATE_LIMIT_BLOCK,
    RATE_LIMIT_WINDOW,
    _check_rate_limit,
    _get_lockout_duration,
    _login_attempts,
    check_password,
    hash_password,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Passwords: printable ASCII, 1-72 chars (bcrypt truncates at 72 bytes)
_passwords = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=72,
)

# Distinct password pairs: (correct, wrong) where wrong != correct
_password_pairs = st.tuples(_passwords, _passwords).filter(
    lambda p: p[0] != p[1]
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    @given(password=_passwords)
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_correct_password_always_passes(self, password):
        """hash → check with the same password must always return True."""
        h = hash_password(password)
        assert check_password(password, h) is True

    @given(pair=_password_pairs)
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_wrong_password_always_fails(self, pair):
        """hash → check with a *different* password must always return False."""
        correct, wrong = pair
        h = hash_password(correct)
        assert check_password(wrong, h) is False

    @given(password=_passwords)
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_hash_is_unique_each_call(self, password):
        """bcrypt gensalt means two hashes of the same password differ."""
        h1 = hash_password(password)
        h2 = hash_password(password)
        assert h1 != h2

    @given(password=_passwords)
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_hash_always_starts_with_bcrypt_prefix(self, password):
        h = hash_password(password)
        assert h.startswith("$2b$")

    def test_check_password_bad_hash_returns_false(self):
        """Malformed hash must return False, not raise."""
        assert check_password("anything", "not-a-bcrypt-hash") is False

    def test_check_password_empty_hash_returns_false(self):
        assert check_password("anything", "") is False


# ---------------------------------------------------------------------------
# Lockout duration monotonicity
# ---------------------------------------------------------------------------


class TestLockoutDuration:
    @given(n=st.integers(min_value=0, max_value=100))
    def test_duration_is_non_negative(self, n):
        assert _get_lockout_duration(n) >= 0

    @given(
        low=st.integers(min_value=0, max_value=50),
        high=st.integers(min_value=0, max_value=50),
    )
    def test_more_failures_never_reduces_lockout(self, low, high):
        """Higher failure count must never result in a shorter lockout."""
        a, b = min(low, high), max(low, high)
        assert _get_lockout_duration(a) <= _get_lockout_duration(b)

    def test_below_first_threshold_has_no_lockout(self):
        first_threshold = LOCKOUT_THRESHOLDS[0][0]
        assert _get_lockout_duration(first_threshold - 1) == 0

    def test_at_each_threshold_lockout_matches_spec(self):
        for threshold, expected_secs in LOCKOUT_THRESHOLDS:
            assert _get_lockout_duration(threshold) == expected_secs

    @given(n=st.integers(min_value=15))
    def test_above_max_threshold_clamps_to_max(self, n):
        max_lockout = max(secs for _, secs in LOCKOUT_THRESHOLDS)
        assert _get_lockout_duration(n) == max_lockout


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def setup_method(self):
        _login_attempts.clear()

    def test_fresh_ip_is_allowed(self):
        allowed, warn = _check_rate_limit("1.2.3.4")
        assert allowed is True
        assert warn is False

    @given(count=st.integers(min_value=1, max_value=RATE_LIMIT_BLOCK - 1))
    def test_below_hard_block_is_allowed(self, count):
        """Any count strictly below RATE_LIMIT_BLOCK must be allowed."""
        ip = f"10.0.0.{count % 254 + 1}"
        _login_attempts[ip] = [time.time()] * count
        allowed, _ = _check_rate_limit(ip)
        assert allowed is True

    def test_at_hard_block_is_rejected(self):
        ip = "10.0.1.1"
        _login_attempts[ip] = [time.time()] * RATE_LIMIT_BLOCK
        allowed, _ = _check_rate_limit(ip)
        assert allowed is False

    @given(extra=st.integers(min_value=0, max_value=20))
    def test_above_hard_block_is_rejected(self, extra):
        ip = "10.0.2.1"
        _login_attempts[ip] = [time.time()] * (RATE_LIMIT_BLOCK + extra)
        allowed, _ = _check_rate_limit(ip)
        assert allowed is False

    def test_expired_attempts_are_pruned(self):
        ip = "10.0.3.1"
        old_time = time.time() - RATE_LIMIT_WINDOW - 1
        _login_attempts[ip] = [old_time] * RATE_LIMIT_BLOCK
        allowed, _ = _check_rate_limit(ip)
        assert allowed is True


# ---------------------------------------------------------------------------
# CSRF token properties
# ---------------------------------------------------------------------------


class TestCSRFToken:
    def test_token_is_64_char_hex(self, app):
        with app.test_request_context():
            from flask import session

            from monitor.auth import generate_csrf_token

            with app.test_client() as c:
                with c.session_transaction() as sess:
                    pass
                with app.test_request_context():
                    session["user_id"] = "u1"
                    token = generate_csrf_token()
                    assert len(token) == 64
                    assert all(c in "0123456789abcdef" for c in token)

    def test_successive_tokens_are_unique(self, app):
        tokens = set()
        with app.test_request_context():
            from flask import session

            from monitor.auth import generate_csrf_token

            for _ in range(20):
                with app.test_request_context():
                    session["user_id"] = "u1"
                    tokens.add(generate_csrf_token())
        assert len(tokens) == 20

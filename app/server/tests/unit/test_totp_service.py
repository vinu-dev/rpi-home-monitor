# REQ: SWR-002, SWR-023
# RISK: RISK-002, RISK-011, RISK-020
# SEC: SC-001, SC-011, SC-020
# TEST: TC-004, TC-011, TC-022
"""Pure-logic tests for monitor.services.totp_service.TotpService."""

import time

import pyotp
import pytest

from monitor.services.totp_service import (
    CHALLENGE_TTL_SECONDS,
    TOTP_STEP_SECONDS,
    TotpService,
)


@pytest.fixture
def svc():
    return TotpService(secret_key="test-secret-key-for-totp")


class TestSecretAndUri:
    def test_generate_secret_is_base32(self, svc):
        s = svc.generate_secret()
        assert isinstance(s, str)
        assert len(s) >= 16
        # Round-trip through pyotp to confirm it's a valid TOTP secret.
        pyotp.TOTP(s).now()

    def test_secrets_are_random(self, svc):
        a = svc.generate_secret()
        b = svc.generate_secret()
        assert a != b

    def test_otpauth_uri_includes_user_and_issuer(self, svc):
        s = svc.generate_secret()
        uri = svc.otpauth_uri("alice", s)
        assert uri.startswith("otpauth://totp/Home%20Monitor%3Aalice")
        assert f"secret={s}" in uri
        assert "issuer=Home%20Monitor" in uri


class TestVerifyCode:
    def test_correct_code_accepted(self, svc):
        s = svc.generate_secret()
        now = 1_700_000_000
        code = pyotp.TOTP(s).at(now)
        ok, step = svc.verify_code(s, code, last_step=0, at=now)
        assert ok is True
        assert step == now // TOTP_STEP_SECONDS

    def test_drift_minus_one_step_accepted(self, svc):
        s = svc.generate_secret()
        now = 1_700_000_000
        previous = now - TOTP_STEP_SECONDS
        code = pyotp.TOTP(s).at(previous)
        ok, _ = svc.verify_code(s, code, last_step=0, at=now)
        assert ok is True

    def test_drift_plus_one_step_accepted(self, svc):
        s = svc.generate_secret()
        now = 1_700_000_000
        future = now + TOTP_STEP_SECONDS
        code = pyotp.TOTP(s).at(future)
        ok, _ = svc.verify_code(s, code, last_step=0, at=now)
        assert ok is True

    def test_drift_two_steps_rejected(self, svc):
        s = svc.generate_secret()
        now = 1_700_000_000
        far = now - 2 * TOTP_STEP_SECONDS
        code = pyotp.TOTP(s).at(far)
        ok, _ = svc.verify_code(s, code, last_step=0, at=now)
        assert ok is False

    def test_replay_same_step_rejected(self, svc):
        s = svc.generate_secret()
        now = 1_700_000_000
        code = pyotp.TOTP(s).at(now)
        ok, accepted = svc.verify_code(s, code, last_step=0, at=now)
        assert ok is True
        # Now persist `accepted`. A second submission of the same step
        # number must be rejected even if the code is still in the
        # 30 s window.
        ok2, _ = svc.verify_code(s, code, last_step=accepted, at=now)
        assert ok2 is False

    def test_wrong_code_rejected(self, svc):
        s = svc.generate_secret()
        ok, _ = svc.verify_code(s, "000000", last_step=0, at=1_700_000_000)
        assert ok is False

    def test_garbage_input_rejected(self, svc):
        s = svc.generate_secret()
        for bad in ["", "abc", "12345", "1234567", "12345a"]:
            ok, _ = svc.verify_code(s, bad, last_step=0, at=1_700_000_000)
            assert ok is False, bad


class TestRecoveryCodes:
    def test_generate_returns_unique_plaintexts_and_matching_hashes(self, svc):
        plaintexts, hashes = svc.generate_recovery_codes(count=10)
        assert len(plaintexts) == 10
        assert len(hashes) == 10
        assert len(set(plaintexts)) == 10
        # Every hash should bcrypt-verify against its plaintext.
        import bcrypt

        for pt, h in zip(plaintexts, hashes, strict=True):
            assert bcrypt.checkpw(pt.encode("utf-8"), h.encode("utf-8"))

    def test_consume_correct_code_removes_hash(self, svc):
        plaintexts, hashes = svc.generate_recovery_codes(count=3)
        ok, remaining = svc.consume_recovery_code(plaintexts[1], hashes)
        assert ok is True
        assert len(remaining) == 2

    def test_consume_already_used_code_rejected(self, svc):
        plaintexts, hashes = svc.generate_recovery_codes(count=2)
        ok1, hashes1 = svc.consume_recovery_code(plaintexts[0], hashes)
        assert ok1 is True
        ok2, _ = svc.consume_recovery_code(plaintexts[0], hashes1)
        assert ok2 is False

    def test_consume_wrong_code_rejected(self, svc):
        _plaintexts, hashes = svc.generate_recovery_codes(count=2)
        ok, _ = svc.consume_recovery_code("ABCD-EFGH-JKMN-PQRS", hashes)
        assert ok is False

    def test_consume_accepts_unhyphenated_form(self, svc):
        plaintexts, hashes = svc.generate_recovery_codes(count=1)
        condensed = plaintexts[0].replace("-", "")
        ok, _ = svc.consume_recovery_code(condensed, hashes)
        assert ok is True


class TestChallengeToken:
    def test_round_trip(self, svc):
        token = svc.issue_challenge_token("user-x", at=1_700_000_000)
        result = svc.verify_challenge_token(token, at=1_700_000_010)
        assert result == ("user-x", False)

    def test_with_remote_flag(self, svc):
        token = svc.issue_challenge_token(
            "user-x", require_remote=True, at=1_700_000_000
        )
        result = svc.verify_challenge_token(token, at=1_700_000_010)
        assert result == ("user-x", True)

    def test_expired(self, svc):
        token = svc.issue_challenge_token("user-x", at=1_700_000_000)
        result = svc.verify_challenge_token(
            token, at=1_700_000_000 + CHALLENGE_TTL_SECONDS + 1
        )
        assert result is None

    def test_tampered_signature(self, svc):
        token = svc.issue_challenge_token("user-x", at=1_700_000_000)
        bad = token[:-1] + ("0" if token[-1] != "0" else "1")
        assert svc.verify_challenge_token(bad, at=1_700_000_010) is None

    def test_tampered_user_id(self, svc):
        token = svc.issue_challenge_token("user-x", at=1_700_000_000)
        parts = token.split(".")
        parts[1] = "user-evil"
        bad = ".".join(parts)
        assert svc.verify_challenge_token(bad, at=1_700_000_010) is None

    def test_different_secret_keys_dont_share_signatures(self):
        a = TotpService(secret_key="alpha")
        b = TotpService(secret_key="beta")
        token = a.issue_challenge_token("u", at=time.time())
        assert b.verify_challenge_token(token) is None

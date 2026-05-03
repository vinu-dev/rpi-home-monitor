# REQ: SWR-238-A, SWR-238-B, SWR-238-C, SWR-238-D, SWR-238-E
# RISK: RISK-238-1, RISK-238-2, RISK-238-3, RISK-238-4
# SEC: SEC-238-A, SEC-238-B, SEC-238-C, SEC-238-D
# TEST: TC-238-AC-1, TC-238-AC-2, TC-238-AC-3, TC-238-AC-4, TC-238-AC-5,
#       TC-238-AC-6, TC-238-AC-7, TC-238-AC-8, TC-238-AC-14
"""TOTP-based two-factor authentication service (issue #238, ADR-0011).

Pure business logic; no Flask imports. Routes in api/auth_totp.py are
thin HTTP adapters that delegate here.

Responsibilities:
- Provision a fresh TOTP secret + otpauth:// URI for enrollment.
- Verify a six-digit code against a user's secret with ±1 step drift,
  rejecting replay of the same step number.
- Generate one-time recovery codes (plaintext returned once; only
  bcrypt hashes persisted) and verify/consume them.
- Sign/verify a short-lived challenge token bridging the password step
  and the TOTP step of login.
"""

import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import quote

import bcrypt
import pyotp

log = logging.getLogger("monitor.services.totp_service")

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_GROUPS = 4
RECOVERY_CODE_GROUP_LEN = 4
RECOVERY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# RFC 6238 step window. We accept current step ±1 (≈30 s) for clock
# drift; ±2 is rejected so a stolen code that's > 30 s old can't be
# replayed.
TOTP_DRIFT_STEPS = 1
TOTP_STEP_SECONDS = 30

# Challenge token lifetime — the user has 5 minutes between password
# success and submitting their TOTP code.
CHALLENGE_TTL_SECONDS = 300


class TotpService:
    """Service-layer logic for TOTP enrollment, verification, recovery."""

    def __init__(self, secret_key: str, issuer: str = "Home Monitor"):
        if not secret_key:
            raise ValueError("TotpService requires a non-empty secret_key")
        self._secret_key = secret_key.encode("utf-8")
        self._issuer = issuer

    # ---- Secret provisioning ----------------------------------------

    @staticmethod
    def generate_secret() -> str:
        """Return a fresh base32-encoded TOTP secret (160 bits)."""
        return pyotp.random_base32()

    def otpauth_uri(self, username: str, secret: str) -> str:
        """Build an ``otpauth://totp/...`` URI for QR-code rendering."""
        label = f"{self._issuer}:{username}"
        return (
            "otpauth://totp/"
            f"{quote(label, safe='')}"
            f"?secret={secret}"
            f"&issuer={quote(self._issuer, safe='')}"
            "&algorithm=SHA1&digits=6&period=30"
        )

    # ---- Code verification ------------------------------------------

    def verify_code(
        self,
        secret: str,
        code: str,
        last_step: int = 0,
        at: float | None = None,
    ) -> tuple[bool, int]:
        """Verify a six-digit TOTP code against ``secret``.

        Args:
            secret: The user's base32 TOTP secret.
            code: Six-digit code submitted by the user.
            last_step: The most recently accepted step number for this
                user. A submitted step ≤ ``last_step`` is rejected as
                replay.
            at: Unix-epoch seconds to use as "now" (overrideable for
                deterministic tests).

        Returns:
            ``(ok, accepted_step)``. ``accepted_step`` is the step number
            the caller MUST persist to ``user.last_totp_step`` on success
            so subsequent attempts in the same window can't replay it.
            On failure, ``accepted_step`` is 0.
        """
        if not secret or not code:
            return False, 0
        cleaned = code.strip().replace(" ", "").replace("-", "")
        if len(cleaned) != 6 or not cleaned.isdigit():
            return False, 0

        now = at if at is not None else time.time()
        current_step = int(now // TOTP_STEP_SECONDS)
        totp = pyotp.TOTP(secret)
        for offset in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
            candidate_step = current_step + offset
            if candidate_step <= last_step:
                # Replay or pre-replay: never accept a step we've
                # already used or one earlier than that.
                continue
            expected = totp.at(candidate_step * TOTP_STEP_SECONDS)
            if hmac.compare_digest(expected, cleaned):
                return True, candidate_step
        return False, 0

    # ---- Recovery codes ---------------------------------------------

    @staticmethod
    def generate_recovery_codes(
        count: int = RECOVERY_CODE_COUNT,
    ) -> tuple[list[str], list[str]]:
        """Return ``(plaintext_codes, bcrypt_hashes)``.

        Plaintext codes are formatted ``xxxx-xxxx-xxxx-xxxx``. Only the
        hashes should be persisted; plaintext is returned to the user
        exactly once.
        """
        plaintexts: list[str] = []
        hashes: list[str] = []
        for _ in range(count):
            groups = [
                "".join(
                    secrets.choice(RECOVERY_CODE_ALPHABET)
                    for _ in range(RECOVERY_CODE_GROUP_LEN)
                )
                for _ in range(RECOVERY_CODE_GROUPS)
            ]
            code = "-".join(groups)
            plaintexts.append(code)
            hashes.append(
                bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")
            )
        return plaintexts, hashes

    @staticmethod
    def consume_recovery_code(
        code: str,
        hashes: list[str],
    ) -> tuple[bool, list[str]]:
        """Try to match ``code`` against any hash in ``hashes``.

        Returns ``(matched, remaining_hashes)``. On match, the matched
        hash is removed from the returned list so the caller can
        persist single-use semantics. Lookup is constant-time across
        all stored hashes (no early return on first match) to limit
        leaking which slot was hit.
        """
        if not code or not hashes:
            return False, list(hashes)
        cleaned = code.strip().replace(" ", "").upper()
        # Accept dashed and undashed forms.
        if "-" not in cleaned and len(cleaned) == (
            RECOVERY_CODE_GROUPS * RECOVERY_CODE_GROUP_LEN
        ):
            cleaned = "-".join(
                cleaned[i : i + RECOVERY_CODE_GROUP_LEN]
                for i in range(0, len(cleaned), RECOVERY_CODE_GROUP_LEN)
            )

        matched_index = -1
        for i, h in enumerate(hashes):
            try:
                if (
                    bcrypt.checkpw(cleaned.encode("utf-8"), h.encode("utf-8"))
                    and matched_index == -1
                ):
                    matched_index = i
            except (ValueError, TypeError):
                continue
        if matched_index == -1:
            return False, list(hashes)
        return True, [h for i, h in enumerate(hashes) if i != matched_index]

    # ---- Challenge token --------------------------------------------

    def issue_challenge_token(
        self,
        user_id: str,
        require_remote: bool = False,
        at: float | None = None,
    ) -> str:
        """Mint a signed token binding the password step to the TOTP step.

        Returns ``<issued_at>.<user_id>.<flag>.<hex_signature>``. The
        signature is HMAC-SHA256 over the first three fields with a
        sub-key derived from the Flask SECRET_KEY (so the session
        cookie and the challenge token never share the raw key).
        """
        issued_at = int(at if at is not None else time.time())
        flag = "1" if require_remote else "0"
        body = f"{issued_at}.{user_id}.{flag}"
        sig = self._sign(body)
        return f"{body}.{sig}"

    def verify_challenge_token(
        self,
        token: str,
        at: float | None = None,
    ) -> tuple[str, bool] | None:
        """Verify a token. Returns ``(user_id, require_remote)`` or None."""
        if not token or token.count(".") != 3:
            return None
        try:
            issued_at_str, user_id, flag, sig = token.split(".")
            issued_at = int(issued_at_str)
        except (ValueError, TypeError):
            return None
        body = f"{issued_at_str}.{user_id}.{flag}"
        expected = self._sign(body)
        if not hmac.compare_digest(expected, sig):
            return None
        now = int(at if at is not None else time.time())
        if now - issued_at > CHALLENGE_TTL_SECONDS:
            return None
        if now < issued_at - 5:  # pragma: no cover — clock skew safety
            return None
        return user_id, flag == "1"

    # ---- Internals --------------------------------------------------

    def _sign(self, body: str) -> str:
        # HKDF-style sub-key derivation from the Flask SECRET_KEY so a
        # leak of one of the derived keys can't be traded for the
        # session-cookie key directly.
        sub_key = hashlib.sha256(self._secret_key + b"|totp-challenge|v1").digest()
        return hmac.new(sub_key, body.encode("utf-8"), hashlib.sha256).hexdigest()

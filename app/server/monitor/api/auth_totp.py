# REQ: SWR-238-A, SWR-238-B, SWR-238-D, SWR-238-E
# RISK: RISK-238-1, RISK-238-2, RISK-238-4
# SEC: SEC-238-A, SEC-238-B, SEC-238-D
# TEST: TC-238-AC-2, TC-238-AC-6, TC-238-AC-8, TC-238-AC-12, TC-238-AC-14
"""TOTP enrollment / disable / recovery-code endpoints (issue #238).

Thin HTTP adapters; business logic lives in
``monitor.services.totp_service.TotpService``.

Endpoints:
- POST /api/v1/auth/totp/enroll/start    — provision a fresh secret
- POST /api/v1/auth/totp/enroll/confirm  — confirm code, persist, issue
                                           recovery codes (once)
- POST /api/v1/auth/totp/disable         — disable, requires password +
                                           TOTP/recovery
- POST /api/v1/auth/totp/recovery-codes/regenerate
- POST /api/v1/users/<id>/totp/reset     — admin reset for another user
"""

import time
from datetime import UTC, datetime

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import (
    admin_required,
    check_password,
    csrf_protect,
    login_required,
)

auth_totp_bp = Blueprint("auth_totp", __name__)
users_totp_bp = Blueprint("users_totp", __name__)

# Pending-enrollment scratchpad: keyed by user id, holds the freshly
# generated secret until the user confirms it with a valid code.
# Stored on the Flask session so it survives the round-trip without
# touching disk.
_PENDING_SESSION_KEY = "totp_pending_secret"


def _audit():
    return getattr(current_app, "audit", None)


def _user_or_401():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return current_app.store.get_user(user_id)


@auth_totp_bp.route("/enroll/start", methods=["POST"])
@login_required
@csrf_protect
def enroll_start():
    """Provision a fresh TOTP secret + otpauth URI for the current user.

    The secret is NOT persisted to ``user.totp_secret`` until the user
    proves they entered it correctly via /enroll/confirm. We stash the
    pending secret on the session so a refresh of the enroll page
    rotates the secret cleanly without leaving stale state on disk.
    """
    user = _user_or_401()
    if not user:
        return jsonify({"error": "Authentication required"}), 401

    totp = current_app.totp_service
    secret = totp.generate_secret()
    session[_PENDING_SESSION_KEY] = secret
    return jsonify(
        {
            "secret": secret,
            "otpauth_uri": totp.otpauth_uri(user.username, secret),
            "issuer": "Home Monitor",
            "username": user.username,
        }
    ), 200


@auth_totp_bp.route("/enroll/confirm", methods=["POST"])
@login_required
@csrf_protect
def enroll_confirm():
    """Confirm the pending secret with a valid code, then persist."""
    user = _user_or_401()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    if user.totp_enabled:
        return jsonify({"error": "Two-factor authentication is already enabled"}), 409

    pending = session.get(_PENDING_SESSION_KEY, "")
    if not pending:
        return jsonify({"error": "Start enrollment before confirming"}), 400

    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    totp = current_app.totp_service
    ok, accepted_step = totp.verify_code(pending, code, last_step=0)
    if not ok:
        return jsonify({"error": "That code didn't match. Try again."}), 400

    plaintexts, hashes = totp.generate_recovery_codes()
    user.totp_secret = pending
    user.totp_enabled = True
    user.recovery_code_hashes = hashes
    user.last_totp_step = accepted_step
    current_app.store.save_user(user)
    session.pop(_PENDING_SESSION_KEY, None)

    audit = _audit()
    if audit:
        audit.log_event(
            "TOTP_ENROLLED",
            user=user.username,
            ip=request.remote_addr or "",
            detail=f"recovery codes issued: {len(plaintexts)}",
        )

    return jsonify(
        {
            "enabled": True,
            "enabled_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recovery_codes": plaintexts,
        }
    ), 200


def _verify_password_and_second_factor(user, data) -> tuple[bool, str]:
    """Shared check for disable and regenerate-recovery-codes: must
    submit current password AND a valid TOTP code (or recovery code)."""
    password = data.get("password", "")
    code = (data.get("code") or "").strip()
    use_recovery = bool(data.get("recovery"))

    if not password or not check_password(password, user.password_hash):
        return False, "Password incorrect"

    totp = current_app.totp_service
    if use_recovery:
        ok, remaining = totp.consume_recovery_code(code, user.recovery_code_hashes)
        if not ok:
            return False, "Recovery code rejected"
        user.recovery_code_hashes = remaining
        return True, ""

    if not user.totp_secret:
        return False, "No TOTP secret configured"
    ok, accepted_step = totp.verify_code(
        user.totp_secret, code, last_step=user.last_totp_step
    )
    if not ok:
        return False, "TOTP code rejected"
    user.last_totp_step = accepted_step
    return True, ""


@auth_totp_bp.route("/disable", methods=["POST"])
@login_required
@csrf_protect
def disable():
    """Disable 2FA on the current user's account."""
    user = _user_or_401()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    if not user.totp_enabled:
        return jsonify({"error": "Two-factor authentication is not enabled"}), 409

    data = request.get_json(silent=True) or {}
    ok, err = _verify_password_and_second_factor(user, data)
    if not ok:
        return jsonify({"error": err}), 401

    user.totp_secret = ""
    user.totp_enabled = False
    user.recovery_code_hashes = []
    user.last_totp_step = 0
    current_app.store.save_user(user)

    audit = _audit()
    if audit:
        audit.log_event(
            "TOTP_DISABLED",
            user=user.username,
            ip=request.remote_addr or "",
            detail="self-disable",
        )
    return jsonify({"enabled": False}), 200


@auth_totp_bp.route("/recovery-codes/regenerate", methods=["POST"])
@login_required
@csrf_protect
def regenerate_recovery_codes():
    """Generate a fresh batch of recovery codes; old hashes are dropped."""
    user = _user_or_401()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    if not user.totp_enabled:
        return jsonify({"error": "Two-factor authentication is not enabled"}), 409

    data = request.get_json(silent=True) or {}
    ok, err = _verify_password_and_second_factor(user, data)
    if not ok:
        return jsonify({"error": err}), 401

    totp = current_app.totp_service
    plaintexts, hashes = totp.generate_recovery_codes()
    user.recovery_code_hashes = hashes
    current_app.store.save_user(user)

    audit = _audit()
    if audit:
        audit.log_event(
            "TOTP_RECOVERY_CODES_REGENERATED",
            user=user.username,
            ip=request.remote_addr or "",
            detail=f"issued {len(plaintexts)}",
        )
    return jsonify({"recovery_codes": plaintexts}), 200


@auth_totp_bp.route("/status", methods=["GET"])
@login_required
def status():
    """Return the current user's 2FA state for the Settings page."""
    user = _user_or_401()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    return jsonify(
        {
            "enabled": user.totp_enabled,
            "recovery_codes_remaining": len(user.recovery_code_hashes),
        }
    ), 200


# --- login verify (2FA second factor) -------------------------------------------


@auth_totp_bp.route("/verify", methods=["POST"])
def verify():
    """Complete the TOTP 2FA challenge during login (step 2 of 2).

    Request:
        challenge_token: from cookie or JSON body
        code: six-digit TOTP code or recovery code
        recovery: boolean flag if submitting a recovery code

    On success: creates the session (like a normal login), returns CSRF token.
    On failure: increments failed_logins + lockout counter on the user.
    """
    from monitor.auth import (
        _check_rate_limit,
        _get_lockout_duration,
        _record_attempt,
        generate_csrf_token,
    )

    ip = request.remote_addr or ""
    audit = _audit()

    # Get challenge token (try cookie first, then body)
    challenge_token = (
        request.cookies.get("totp_challenge")
        or (request.get_json(silent=True) or {}).get("challenge_token", "")
    )
    if not challenge_token:
        return jsonify({"error": "Challenge token required"}), 400

    totp = current_app.totp_service
    result = totp.verify_challenge_token(challenge_token)
    if not result:
        if audit:
            audit.log_event("LOGIN_2FA_FAILED", ip=ip, detail="challenge token invalid/expired")
        return jsonify({"error": "Your sign-in expired. Please sign in again."}), 401

    user_id, require_remote = result
    user = current_app.store.get_user(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Rate limiting on TOTP step (combined with password step)
    allowed, warn = _check_rate_limit(ip)
    if not allowed:
        if audit:
            audit.log_event("LOGIN_BLOCKED", user=user.username, ip=ip, detail="rate limited (hard block)")
        return jsonify({"error": "Too many login attempts. Try again later."}), 429

    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    use_recovery = bool(data.get("recovery"))

    if not code:
        _record_attempt(ip)
        return jsonify({"error": "Code required"}), 400

    # User must have TOTP enabled or we're in a remote-policy override
    if not user.totp_enabled and not require_remote:
        _record_attempt(ip)
        if audit:
            audit.log_event("LOGIN_2FA_FAILED", user=user.username, ip=ip, detail="user has no TOTP enrolled")
        return jsonify({"error": "Two-factor authentication is not enabled"}), 401

    # If user has no TOTP but remote policy requires it, they can't log in
    if require_remote and not user.totp_enabled:
        if audit:
            audit.log_event(
                "LOGIN_2FA_FAILED",
                user=user.username,
                ip=ip,
                detail="remote policy requires 2FA; user not enrolled",
            )
        return jsonify({
            "error": "Two-factor authentication is required for remote access. Please enroll on the local network first."
        }), 401

    # Verify code (TOTP or recovery)
    ok = False
    if use_recovery:
        ok, remaining = totp.consume_recovery_code(code, user.recovery_code_hashes)
        if ok:
            user.recovery_code_hashes = remaining
            if audit:
                audit.log_event(
                    "TOTP_RECOVERY_USED",
                    user=user.username,
                    ip=ip,
                    detail=f"recovery code consumed; {len(remaining)} remaining",
                )
    else:
        ok, accepted_step = totp.verify_code(
            user.totp_secret, code, last_step=user.last_totp_step
        )
        if ok:
            user.last_totp_step = accepted_step

    if not ok:
        _record_attempt(ip)
        user.failed_logins += 1
        lockout_secs = _get_lockout_duration(user.failed_logins)
        if lockout_secs > 0:
            from datetime import timedelta
            user.locked_until = (
                datetime.now(UTC) + timedelta(seconds=lockout_secs)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        current_app.store.save_user(user)
        if audit:
            audit.log_event("LOGIN_2FA_FAILED", user=user.username, ip=ip, detail="code rejected")
        return jsonify({"error": "That code didn't match. Try again."}), 401

    # Success: create session (replicate the normal login flow)
    user.failed_logins = 0
    user.locked_until = ""
    current_app.store.save_user(user)

    session.clear()
    session["user_id"] = user.id
    session["username"] = user.username
    session["role"] = user.role
    session["created_at"] = time.time()
    session["last_active"] = time.time()
    session["must_change_password"] = bool(user.must_change_password)

    csrf_token = generate_csrf_token()

    if audit:
        audit.log_event("LOGIN_SUCCESS", user=user.username, ip=ip, detail="TOTP verified, session created")

    response_data = {
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        },
        "csrf_token": csrf_token,
    }

    if user.must_change_password:
        response_data["must_change_password"] = True

    # Clear the challenge token cookie
    response = jsonify(response_data)
    response.set_cookie("totp_challenge", "", max_age=0)
    return response, 200


# --- admin reset --------------------------------------------------------


@users_totp_bp.route("/<user_id>/totp/reset", methods=["POST"])
@admin_required
@csrf_protect
def admin_reset(user_id: str):
    """Admin clears another user's TOTP state (locked-out recovery)."""
    if user_id == session.get("user_id"):
        return jsonify({"error": "Use self-disable to remove your own 2FA"}), 400

    target = current_app.store.get_user(user_id)
    if not target:
        return jsonify({"error": "User not found"}), 404

    # Last-admin guard: spec §1.5 — an admin can't reset the only
    # other admin's TOTP if doing so would leave the system with zero
    # admins able to log in remotely. We guard the simpler "this is
    # the last admin" case in line with the password-reset rail.
    if target.role == "admin":
        admins = [u for u in current_app.store.get_users() if u.role == "admin"]
        if len(admins) <= 1:
            return jsonify({"error": "Cannot reset the only admin's 2FA"}), 400

    target.totp_secret = ""
    target.totp_enabled = False
    target.recovery_code_hashes = []
    target.last_totp_step = 0
    current_app.store.save_user(target)

    audit = _audit()
    if audit:
        audit.log_event(
            "TOTP_RESET_BY_ADMIN",
            user=session.get("username", ""),
            ip=request.remote_addr or "",
            detail=f"reset 2FA for {target.username}",
        )
    return jsonify({"reset": True}), 200

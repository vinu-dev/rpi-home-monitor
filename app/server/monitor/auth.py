"""
Authentication and authorization module (ADR-0011).

Handles login/logout, session management, CSRF protection,
role-based access control (admin/viewer), rate limiting, and
account lockout.

Security features:
- bcrypt password hashing (cost 12)
- Secure/HttpOnly/SameSite=Strict session cookies
- CSRF tokens on state-changing requests
- Session timeout (60 min idle, 24 hr absolute)
- Rate limiting on login (5 attempts/min per IP)
- Exponential account lockout (1/5/30 min after 5/10/15 failures)
- Audit logging of all auth events
"""

import functools
import hashlib
import os
import time
from datetime import UTC, datetime

import bcrypt
from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
    session,
)

auth_bp = Blueprint("auth", __name__)

# In-memory rate limiter: {ip: [timestamp, ...]}
_login_attempts: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 5  # attempts per window
RATE_LIMIT_BLOCK = 10  # block after this many in window

# Account lockout thresholds (ADR-0011)
LOCKOUT_THRESHOLDS = [
    (5, 60),  # 5 failures → 1 min lockout
    (10, 300),  # 10 failures → 5 min lockout
    (15, 1800),  # 15 failures → 30 min lockout
]


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (cost 12)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")


def check_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


def generate_csrf_token() -> str:
    """Generate a random CSRF token and store in session."""
    token = hashlib.sha256(os.urandom(32)).hexdigest()
    session["csrf_token"] = token
    return token


def _check_rate_limit(ip: str) -> tuple[bool, bool]:
    """Check if an IP has exceeded the login rate limit.

    Two-tier system:
    - RATE_LIMIT_MAX (5): soft limit — request allowed but a warning is logged.
    - RATE_LIMIT_BLOCK (10): hard limit — request is rejected (HTTP 429).

    Returns:
        (allowed, warn) — allowed=False means block; warn=True means soft limit hit.
    """
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Remove expired attempts
    attempts = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
    _login_attempts[ip] = attempts

    count = len(attempts)
    if count >= RATE_LIMIT_BLOCK:
        return False, False  # hard block
    if count >= RATE_LIMIT_MAX:
        return True, True  # allowed but warned
    return True, False  # normal


def _record_attempt(ip: str):
    """Record a login attempt for rate limiting."""
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(now)


def _get_lockout_duration(failed_count: int) -> int:
    """Return lockout duration in seconds based on failure count."""
    duration = 0
    for threshold, secs in LOCKOUT_THRESHOLDS:
        if failed_count >= threshold:
            duration = secs
    return duration


def _is_account_locked(user) -> bool:
    """Check if a user account is currently locked out."""
    if not user.locked_until:
        return False
    try:
        locked_until = datetime.fromisoformat(user.locked_until)
        return datetime.now(UTC) < locked_until
    except (ValueError, TypeError):
        return False


def _get_audit_logger():
    """Get the audit logger from the app, if available."""
    return getattr(current_app, "audit", None)


def _is_session_valid() -> bool:
    """Check if the current session is still valid (not expired)."""
    if "user_id" not in session:
        return False

    timeout_minutes = current_app.config.get("SESSION_TIMEOUT_MINUTES", 60)
    last_active = session.get("last_active")
    created_at = session.get("created_at")
    now = time.time()

    # Idle timeout
    if last_active and (now - last_active) > (timeout_minutes * 60):
        return False

    # Absolute timeout (24 hours)
    if created_at and (now - created_at) > 86400:  # noqa: SIM103
        return False

    return True


def login_required(f):
    """Decorator: require authenticated session."""

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _is_session_valid():
            session.clear()
            return jsonify({"error": "Authentication required"}), 401
        session["last_active"] = time.time()
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    """Decorator: require admin role."""

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _is_session_valid():
            session.clear()
            return jsonify({"error": "Authentication required"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "Admin access required"}), 403
        session["last_active"] = time.time()
        return f(*args, **kwargs)

    return decorated


def csrf_protect(f):
    """Decorator: validate CSRF token on state-changing requests."""

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE"):
            token = request.headers.get("X-CSRF-Token") or request.form.get(
                "csrf_token"
            )
            if not token or token != session.get("csrf_token"):
                return jsonify({"error": "Invalid CSRF token"}), 403
        return f(*args, **kwargs)

    return decorated


@auth_bp.route("/login", methods=["POST"])
def login():
    """Authenticate user and create session."""
    ip = request.remote_addr or ""
    audit = _get_audit_logger()

    # Rate limiting (two-tier: warn at 5, block at 10)
    allowed, warn = _check_rate_limit(ip)
    if not allowed:
        if audit:
            audit.log_event("LOGIN_BLOCKED", ip=ip, detail="rate limited (hard block)")
        return jsonify({"error": "Too many login attempts. Try again later."}), 429
    if warn and audit:
        audit.log_event("LOGIN_RATE_WARN", ip=ip, detail="approaching rate limit")

    data = request.get_json(silent=True)
    if not data or not data.get("username") or not data.get("password"):
        _record_attempt(ip)
        return jsonify({"error": "Username and password required"}), 400

    username = data["username"]
    password = data["password"]

    store = current_app.store
    user = store.get_user_by_username(username)

    # Check account lockout before password verification
    if user and _is_account_locked(user):
        _record_attempt(ip)
        if audit:
            audit.log_event(
                "LOGIN_BLOCKED", user=username, ip=ip, detail="account locked"
            )
        return jsonify(
            {"error": "Account is temporarily locked. Try again later."}
        ), 423

    if not user or not check_password(password, user.password_hash):
        _record_attempt(ip)
        # Increment failed login counter on the actual user
        if user:
            user.failed_logins += 1
            lockout_secs = _get_lockout_duration(user.failed_logins)
            if lockout_secs > 0:
                from datetime import timedelta

                user.locked_until = (
                    datetime.now(UTC) + timedelta(seconds=lockout_secs)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            store.save_user(user)
        if audit:
            detail = "invalid credentials"
            if user:
                detail += f" (failures: {user.failed_logins})"
            audit.log_event("LOGIN_FAILED", user=username, ip=ip, detail=detail)
        return jsonify({"error": "Invalid username or password"}), 401

    # Successful login — reset lockout state
    user.failed_logins = 0
    user.locked_until = ""
    user.last_login = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.save_user(user)

    # Create session
    session.clear()
    session["user_id"] = user.id
    session["username"] = user.username
    session["role"] = user.role
    session["created_at"] = time.time()
    session["last_active"] = time.time()

    csrf_token = generate_csrf_token()

    if audit:
        audit.log_event("LOGIN_SUCCESS", user=username, ip=ip, detail="session created")

    response_data = {
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        },
        "csrf_token": csrf_token,
    }

    # Signal if password change is required
    if user.must_change_password:
        response_data["must_change_password"] = True

    return jsonify(response_data), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Destroy session."""
    username = session.get("username", "")
    ip = request.remote_addr or ""
    audit = _get_audit_logger()

    session.clear()

    if audit:
        audit.log_event("SESSION_LOGOUT", user=username, ip=ip)

    return jsonify({"message": "Logged out"}), 200


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    """Return current user info and role."""
    return jsonify(
        {
            "user": {
                "id": session.get("user_id"),
                "username": session.get("username"),
                "role": session.get("role"),
            },
            "csrf_token": session.get("csrf_token", ""),
        }
    ), 200

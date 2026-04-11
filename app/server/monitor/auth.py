"""
Authentication and authorization module.

Handles login/logout, session management, CSRF protection,
role-based access control (admin/viewer), and rate limiting.

Security features:
- bcrypt password hashing (cost 12)
- Secure/HttpOnly/SameSite=Strict session cookies
- CSRF tokens on state-changing requests
- Session timeout (30 min idle, 24 hr absolute)
- Rate limiting on login (5 attempts/min, block after 10 failures)
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


def _check_rate_limit(ip: str) -> bool:
    """Check if an IP has exceeded the login rate limit.

    Returns True if the request should be allowed, False if blocked.
    """
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Remove expired attempts
    attempts = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
    _login_attempts[ip] = attempts

    return len(attempts) < RATE_LIMIT_BLOCK


def _record_attempt(ip: str):
    """Record a login attempt for rate limiting."""
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(now)


def _get_audit_logger():
    """Get the audit logger from the app, if available."""
    return getattr(current_app, "audit", None)


def _is_session_valid() -> bool:
    """Check if the current session is still valid (not expired)."""
    if "user_id" not in session:
        return False

    timeout_minutes = current_app.config.get("SESSION_TIMEOUT_MINUTES", 30)
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

    # Rate limiting
    if not _check_rate_limit(ip):
        if audit:
            audit.log_event("LOGIN_FAILED", ip=ip, detail="rate limited")
        return jsonify({"error": "Too many login attempts. Try again later."}), 429

    data = request.get_json(silent=True)
    if not data or not data.get("username") or not data.get("password"):
        _record_attempt(ip)
        return jsonify({"error": "Username and password required"}), 400

    username = data["username"]
    password = data["password"]

    store = current_app.store
    user = store.get_user_by_username(username)

    if not user or not check_password(password, user.password_hash):
        _record_attempt(ip)
        if audit:
            audit.log_event(
                "LOGIN_FAILED", user=username, ip=ip, detail="invalid credentials"
            )
        return jsonify({"error": "Invalid username or password"}), 401

    # Update last login
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

    return jsonify(
        {
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role,
            },
            "csrf_token": csrf_token,
        }
    ), 200


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

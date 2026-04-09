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
from flask import Blueprint

auth_bp = Blueprint("auth", __name__)


# TODO: Implement
# POST /login  - authenticate user, create session
# POST /logout - destroy session
# GET  /me     - current user info and role

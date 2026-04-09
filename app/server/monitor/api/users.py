"""
User management API.

Endpoints:
  GET    /users              - list users (admin)
  POST   /users              - create user (admin)
  DELETE /users/<id>         - delete user (admin)
  PUT    /users/<id>/password - change password (admin or self)

Roles: admin (full access), viewer (read-only).
Passwords stored as bcrypt hashes (cost 12).
"""
from flask import Blueprint

users_bp = Blueprint("users", __name__)

# TODO: Implement endpoints

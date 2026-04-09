"""
Settings API.

Endpoints:
  GET /settings - current settings
  PUT /settings - update settings (admin only)

Settings stored in /data/config/settings.json.
Survives OTA updates (on data partition).
"""
from flask import Blueprint

settings_bp = Blueprint("settings", __name__)

# TODO: Implement endpoints

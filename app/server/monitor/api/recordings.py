"""
Recordings API.

Endpoints:
  GET    /recordings/<cam-id>?date=YYYY-MM-DD  - list clips for a camera/date
  GET    /recordings/<cam-id>/timeline          - timeline data for date
  GET    /recordings/<cam-id>/latest            - most recent clip
  DELETE /recordings/<cam-id>/<filename>        - delete a clip (admin)
"""
from flask import Blueprint

recordings_bp = Blueprint("recordings", __name__)

# TODO: Implement endpoints

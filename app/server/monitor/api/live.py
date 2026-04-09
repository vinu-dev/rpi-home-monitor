"""
Live streaming API.

Endpoints:
  GET /live/<cam-id>/stream.m3u8  - HLS playlist for live view
  GET /live/<cam-id>/snapshot     - current frame as JPEG

Note: HLS segment files (.ts) are served directly by nginx,
not through Flask. This blueprint handles playlist generation
and snapshot extraction.
"""
from flask import Blueprint

live_bp = Blueprint("live", __name__)

# TODO: Implement endpoints

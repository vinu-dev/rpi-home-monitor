"""
Over-the-Air update API.

Endpoints:
  POST /ota/server/upload     - upload .swu image for server (admin)
  POST /ota/camera/<id>/push  - push update to camera (admin)
  GET  /ota/status            - update status for all devices

OTA uses swupdate with A/B partition scheme.
Images must be Ed25519 signed — unsigned images are rejected.
"""
from flask import Blueprint

ota_bp = Blueprint("ota", __name__)

# TODO: Implement endpoints

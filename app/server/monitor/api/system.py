"""
System health and info API.

Endpoints:
  GET /system/health  - CPU temp, CPU%, RAM%, disk usage
  GET /system/storage - storage breakdown (total, used, free, per-camera)
  GET /system/info    - firmware version, uptime, hostname, network
"""
from flask import Blueprint

system_bp = Blueprint("system", __name__)

# TODO: Implement endpoints

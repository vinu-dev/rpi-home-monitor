"""
Storage management service — handles loop recording and cleanup.

Responsibilities:
- Monitor /data partition usage every 60 seconds
- When usage exceeds threshold (default 90%):
  - Find and delete oldest clips across all cameras
  - Never delete clips < 24 hours old (stop recording instead)
- Provide storage stats: total, used, free, per-camera breakdown
- Track clip count, oldest clip date, newest clip date
"""

# TODO: Implement StorageManager class

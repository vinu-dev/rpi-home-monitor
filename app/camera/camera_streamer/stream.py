"""
RTSPS stream manager.

Manages the ffmpeg process that captures video from v4l2
and streams it to the home server over RTSPS (RTSP + TLS).

Features:
- Auto-reconnect on server disconnect (exponential backoff, max 60s)
- Health monitoring (check ffmpeg process alive)
- Graceful shutdown on SIGTERM
- Uses mTLS client certificate for authentication

ffmpeg command (conceptual):
  ffmpeg -f v4l2 -input_format h264 -video_size 1920x1080 -framerate 25
         -i /dev/video0 -c:v copy -f rtsp -rtsp_transport tcp
         -tls_cert /data/certs/client.crt -tls_key /data/certs/client.key
         rtsps://<server>:8554/<stream-name>
"""

# TODO: Implement StreamManager class

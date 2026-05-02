# REQ: SWR-006, SWR-012, SWR-053; RISK: RISK-001, RISK-007; SEC: SC-004; TEST: TC-001, TC-012, TC-047
# Enable features needed for RTSP streaming with mTLS (ADR-0009)
#
# - openssl: TLS support for RTSPS muxer (-tls_cert, -tls_key, -tls_ca_cert)
# - v4l2: V4L2 device access for camera capture
# - gpl: required for x264 and other GPL-licensed codecs
#
# This replaces rpidistro-ffmpeg 4.3.4 which lacked RTSP muxer TLS options
# and could not probe raw H.264 streams from libcamera-vid.
PACKAGECONFIG:append = " openssl v4l2 gpl"

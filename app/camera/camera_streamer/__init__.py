# REQ: SWR-012, SWR-045; RISK: RISK-001, RISK-021; SEC: SC-021; TEST: TC-005, TC-042
"""
RPi Home Monitor - Camera Streamer Application

Runs on RPi Zero 2W + PiHut ZeroCam. Captures video via v4l2,
streams to the home server via RTSPS (TLS), advertises via mDNS,
and accepts OTA updates pushed from the server.
"""

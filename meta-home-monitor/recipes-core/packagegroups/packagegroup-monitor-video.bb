# REQ: SWR-046, SWR-049, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044, TC-047
SUMMARY = "Video/streaming packages for Home Monitor server"
DESCRIPTION = "FFmpeg, GStreamer, and v4l for RTSP reception and recording."
LICENSE = "MIT"

inherit packagegroup

RDEPENDS:${PN} = " \
    ffmpeg \
    v4l-utils \
    mediamtx \
    gstreamer1.0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    "

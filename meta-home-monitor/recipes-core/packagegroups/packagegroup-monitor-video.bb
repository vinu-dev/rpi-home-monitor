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

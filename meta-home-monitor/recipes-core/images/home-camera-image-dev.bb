# REQ: SWR-046, SWR-049, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044, TC-047
# =============================================================
# home-camera-image-dev.bb — Development image for RPi Zero 2W camera
#
# Build: bitbake home-camera-image-dev
# =============================================================

require home-camera-image.inc

SUMMARY .= " (Development)"

# --- Dev features ---
EXTRA_IMAGE_FEATURES += "debug-tweaks ssh-server-openssh tools-debug"

# --- Dev tools ---
IMAGE_INSTALL += " \
    strace \
    tcpdump \
    iperf3 \
    lsof \
    tmux \
    less \
    iproute2 \
    e2fsprogs-resize2fs \
    parted \
    "

# --- Debug logging (LOG_LEVEL=DEBUG for app services) ---
IMAGE_INSTALL += "monitor-dev-config"

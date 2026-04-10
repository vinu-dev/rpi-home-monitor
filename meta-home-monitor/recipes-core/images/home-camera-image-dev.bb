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
    "

# --- Debug logging (LOG_LEVEL=DEBUG for app services) ---
IMAGE_INSTALL += "monitor-dev-config"

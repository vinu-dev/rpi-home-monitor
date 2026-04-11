# =============================================================
# home-monitor-image-dev.bb — Development image for RPi 4B server
#
# Includes debug-tweaks (root login, no password), dev tools,
# and SSH access for fast development iteration.
#
# Build: bitbake home-monitor-image-dev
# =============================================================

require home-monitor-image.inc

SUMMARY .= " (Development)"

# --- Dev features ---
EXTRA_IMAGE_FEATURES += "debug-tweaks ssh-server-openssh tools-debug"

# --- Dev tools ---
IMAGE_INSTALL += " \
    python3-pip \
    gdb \
    strace \
    tcpdump \
    iperf3 \
    lsof \
    tmux \
    less \
    tree \
    iproute2 \
    openssh-sftp-server \
    e2fsprogs-resize2fs \
    parted \
    "

# --- Debug logging (LOG_LEVEL=DEBUG for app services) ---
IMAGE_INSTALL += "monitor-dev-config"

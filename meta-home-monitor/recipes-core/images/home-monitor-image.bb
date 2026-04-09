# =============================================================
# home-monitor-image.bb
# Custom image for RPi 4B Home Monitoring Server
# =============================================================
SUMMARY = "Raspberry Pi 4B Home Monitoring Server Image"
DESCRIPTION = "A minimal image with video streaming, storage, \
and web interface for home security camera monitoring. \
Designed to receive feeds from RPi Zero 2W cameras."
LICENSE = "MIT"

inherit core-image

# --- Base system ---
IMAGE_INSTALL += " \
    packagegroup-core-boot \
    packagegroup-core-ssh-openssh \
    "

# --- Networking ---
IMAGE_INSTALL += " \
    wpa-supplicant \
    dhcpcd \
    iw \
    iptables \
    networkmanager \
    avahi-daemon \
    "

# --- Video / Streaming ---
IMAGE_INSTALL += " \
    ffmpeg \
    v4l-utils \
    gstreamer1.0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    "

# --- Web server for mobile access ---
IMAGE_INSTALL += " \
    nginx \
    python3 \
    python3-flask \
    python3-pip \
    python3-bcrypt \
    "

# --- Storage & filesystem ---
IMAGE_INSTALL += " \
    e2fsprogs \
    dosfstools \
    util-linux \
    "

# --- Security ---
IMAGE_INSTALL += " \
    openssl \
    nftables \
    cryptsetup \
    "

# --- System utilities ---
IMAGE_INSTALL += " \
    htop \
    nano \
    curl \
    wget \
    rsync \
    cronie \
    logrotate \
    tzdata \
    "

# --- Python packages for the monitoring app ---
IMAGE_INSTALL += " \
    python3-requests \
    python3-jinja2 \
    "

# --- Our custom monitor server ---
IMAGE_INSTALL += " \
    monitor-server \
    monitor-certs \
    "

# --- Image type: SD card image ---
IMAGE_FSTYPES = "wic.bz2 wic.bmap"

# --- Extra space for video recordings (1GB extra) ---
IMAGE_ROOTFS_EXTRA_SPACE = "1048576"

# =============================================================
# home-camera-image.bb
# Custom image for RPi Zero 2W Camera Node
# =============================================================
SUMMARY = "Raspberry Pi Zero 2W Camera Node Image"
DESCRIPTION = "Minimal image that captures video from the PiHut ZeroCam \
and streams it via RTSP to the RPi 4B home monitoring server."
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
    networkmanager \
    avahi-daemon \
    "

# --- Camera & Video streaming ---
IMAGE_INSTALL += " \
    ffmpeg \
    v4l-utils \
    libcamera \
    libcamera-apps \
    "

# --- Security ---
IMAGE_INSTALL += " \
    openssl \
    nftables \
    cryptsetup \
    "

# --- System utilities (minimal) ---
IMAGE_INSTALL += " \
    htop \
    nano \
    curl \
    python3 \
    tzdata \
    "

# --- Our camera streamer service ---
IMAGE_INSTALL += " \
    camera-streamer \
    "

# --- Image type: SD card image ---
IMAGE_FSTYPES = "wic.bz2 wic.bmap"

# --- Keep image small for Zero 2W ---
IMAGE_ROOTFS_EXTRA_SPACE = "262144"

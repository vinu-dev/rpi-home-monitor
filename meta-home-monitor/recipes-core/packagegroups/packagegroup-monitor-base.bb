SUMMARY = "Base packages for Home Monitor devices"
DESCRIPTION = "Core system packages shared by both server and camera images."
LICENSE = "MIT"

inherit packagegroup

RDEPENDS:${PN} = " \
    packagegroup-core-boot \
    packagegroup-core-ssh-openssh \
    wpa-supplicant \
    iw \
    networkmanager \
    dnsmasq \
    avahi-daemon \
    tzdata \
    tailscale \
    nm-persist \
    htop \
    nano \
    curl \
    "

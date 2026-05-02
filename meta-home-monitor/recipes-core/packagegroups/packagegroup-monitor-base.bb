# REQ: SWR-046, SWR-049, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044, TC-047
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

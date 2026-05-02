# REQ: SWR-046, SWR-049, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044, TC-047
SUMMARY = "Security packages for Home Monitor devices"
DESCRIPTION = "TLS, firewall, disk encryption, and OTA update support."
LICENSE = "MIT"

inherit packagegroup

RDEPENDS:${PN} = " \
    openssl \
    nftables \
    cryptsetup \
    hwrevision \
    sw-versions \
    "

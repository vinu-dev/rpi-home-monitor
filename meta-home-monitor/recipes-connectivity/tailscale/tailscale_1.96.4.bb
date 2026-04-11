SUMMARY = "Tailscale mesh VPN client"
DESCRIPTION = "Zero-config VPN for secure remote access to camera live view"
HOMEPAGE = "https://tailscale.com"
LICENSE = "BSD-3-Clause"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/BSD-3-Clause;md5=550794465ba0ec5312d6919e203a55f9"

SRC_URI = "https://pkgs.tailscale.com/stable/tailscale_${PV}_arm64.tgz;downloadfilename=tailscale_${PV}_arm64.tgz"
SRC_URI[sha256sum] = "a27249bc70d7b37a68f8be7f5c4507ea5f354e592dce43cb5d4f3e742b313c3c"

SRC_URI += "file://tailscaled.service"

S = "${WORKDIR}/tailscale_${PV}_arm64"

# Pre-built Go static binaries — skip compilation
do_compile[noexec] = "1"

INSANE_SKIP:${PN} = "already-stripped ldflags"

do_install() {
    # Binaries
    install -d ${D}${bindir}
    install -m 0755 ${S}/tailscale ${D}${bindir}/tailscale
    install -m 0755 ${S}/tailscaled ${D}${bindir}/tailscaled

    # Systemd service (our custom one with /data state dir)
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/tailscaled.service ${D}${systemd_system_unitdir}/tailscaled.service

    # State directory (persists on /data partition)
    install -d ${D}/data/tailscale
}

inherit systemd

SYSTEMD_SERVICE:${PN} = "tailscaled.service"
SYSTEMD_AUTO_ENABLE = "enable"

# Only for aarch64
COMPATIBLE_HOST = "aarch64.*-linux"

FILES:${PN} += "/data/tailscale"

# =============================================================
# OS branding — customize /etc/os-release for Home Monitor OS
# =============================================================

# Override distro identity fields
NAME = "Home Monitor OS"
ID = "home-monitor"
PRETTY_NAME = "Home Monitor OS ${DISTRO_VERSION} (${DISTRO_CODENAME})"
HOME_URL = "https://github.com/vinu-engineer/rpi-home-monitor"

# Add HOME_URL to the output fields
OS_RELEASE_FIELDS:append = " HOME_URL"

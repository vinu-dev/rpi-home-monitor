# =============================================================
# OS branding — customize /etc/os-release for Home Monitor OS
#
# Fields follow freedesktop.org os-release(5) specification.
# The Flask app reads /etc/os-release to show OS version in dashboard.
# =============================================================

# Override distro identity fields
NAME = "Home Monitor OS"
ID = "home-monitor"
VERSION_ID = "${DISTRO_VERSION}"
PRETTY_NAME = "Home Monitor OS ${DISTRO_VERSION} (${DISTRO_CODENAME})"
HOME_URL = "https://github.com/vinu-dev/rpi-home-monitor"

# BUILD_ID uses ISO 8601 date format (industry standard for build identification)
BUILD_ID = "${DATE}"

# Add all fields to output
OS_RELEASE_FIELDS:append = " HOME_URL VERSION_ID BUILD_ID"

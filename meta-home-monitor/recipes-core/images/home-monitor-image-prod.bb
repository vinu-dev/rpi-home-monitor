# REQ: SWR-046, SWR-049, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044, TC-047
# =============================================================
# home-monitor-image-prod.bb — Production image for RPi 4B server
#
# Hardened: no root password, no debug-tweaks, key-only SSH.
# LUKS encryption on /data partition (ADR-0010).
# This is what gets flashed to production devices.
#
# Build: bitbake home-monitor-image-prod
# =============================================================

require home-monitor-image.inc

SUMMARY .= " (Production)"

# --- Production features: SSH but no debug ---
EXTRA_IMAGE_FEATURES += "ssh-server-openssh"

# No debug-tweaks: root account is locked, must use first-boot wizard

# --- LUKS encryption (ADR-0010) ---
LUKS_ENABLED = "1"
WKS_FILE = "home-monitor-ab-luks.wks"
IMAGE_INSTALL += "cryptsetup"

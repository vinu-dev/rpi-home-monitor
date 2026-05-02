# REQ: SWR-046, SWR-049, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044, TC-047
# =============================================================
# home-camera-image-prod.bb — Production image for RPi Zero 2W camera
#
# LUKS encryption on /data partition with 64MB argon2id (ADR-0010).
#
# Build: bitbake home-camera-image-prod
# =============================================================

require home-camera-image.inc

SUMMARY .= " (Production)"

# --- Production features: SSH but no debug ---
EXTRA_IMAGE_FEATURES += "ssh-server-openssh"

# No debug-tweaks: root locked, managed by server

# --- LUKS encryption (ADR-0010) ---
LUKS_ENABLED = "1"
WKS_FILE = "home-camera-ab-luks.wks"
IMAGE_INSTALL += "cryptsetup"

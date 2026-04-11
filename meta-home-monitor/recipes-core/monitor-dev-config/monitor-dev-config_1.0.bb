# =============================================================
# monitor-dev-config — Dev-only configuration for Home Monitor
#
# Installed ONLY in -dev image variants. Prod images don't
# include this recipe.
#
# What it does:
#   1. Debug logging — LOG_LEVEL=DEBUG for app services
#   2. Dev defaults  — skip setup wizard, pre-provision admin/admin
#                      credentials so dev builds boot straight to a
#                      testable state (see ADR-0007)
#
# Default dev credentials:
#   Server: admin / admin  (auto-created by app on first boot)
#   Camera: admin / admin  (pre-provisioned by this recipe)
# =============================================================
SUMMARY = "Development configuration for Home Monitor"
DESCRIPTION = "Debug logging and dev defaults for dev builds. See ADR-0007."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

inherit systemd

S = "${WORKDIR}"

# Pre-computed PBKDF2-SHA256 hash of "admin" with fixed dev salt.
# Only used in dev builds — prod builds require the setup wizard.
DEV_ADMIN_HASH = "devdefault00000000000000000000000:864af9109b394c877ae9076d96104693c91c04f020fec42481a8ff9680c1c3b4"

do_install() {
    # ── 1. Debug logging drop-ins ──────────────────────────────

    # Monitor server debug logging
    install -d ${D}${sysconfdir}/systemd/system/monitor.service.d
    cat > ${D}${sysconfdir}/systemd/system/monitor.service.d/10-dev-logging.conf << 'CONF'
[Service]
Environment=LOG_LEVEL=DEBUG
CONF

    # Camera streamer debug logging
    install -d ${D}${sysconfdir}/systemd/system/camera-streamer.service.d
    cat > ${D}${sysconfdir}/systemd/system/camera-streamer.service.d/10-dev-logging.conf << 'CONF'
[Service]
Environment=LOG_LEVEL=DEBUG
CONF

    # ── 2. Dev defaults oneshot service ────────────────────────

    install -d ${D}${sysconfdir}/systemd/system
    cat > ${D}${sysconfdir}/systemd/system/monitor-dev-defaults.service << 'UNIT'
[Unit]
Description=Provision dev defaults (skip setup wizard, admin/admin)
# Run once before app services start, only if not already done
ConditionPathExists=!/data/.setup-done
After=local-fs.target
Before=monitor.service camera-streamer.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/monitor-dev-defaults.sh

[Install]
WantedBy=multi-user.target
UNIT

    install -d ${D}${bindir}
    cat > ${D}${bindir}/monitor-dev-defaults.sh << SCRIPT
#!/bin/sh
# Provision dev defaults — only runs on first boot of dev images.
# See ADR-0007: Dev Build Default Credentials.
set -e

echo "[dev-defaults] Provisioning dev defaults..."

# Create data directories
mkdir -p /data/config

# Stamp setup as complete (skips the first-boot wizard)
touch /data/.setup-done

# Camera config with admin/admin password pre-set
if [ ! -f /data/config/camera.conf ]; then
    cat > /data/config/camera.conf << 'CAMCONF'
# Dev defaults — auto-generated, see ADR-0007
SERVER_IP=
SERVER_PORT=8554
STREAM_NAME=stream
WIDTH=1920
HEIGHT=1080
FPS=25
CAMERA_ID=
ADMIN_USERNAME=admin
ADMIN_PASSWORD=${DEV_ADMIN_HASH}
CAMCONF
    echo "[dev-defaults] Camera config created with admin/admin"
else
    echo "[dev-defaults] Camera config already exists, skipping"
fi

echo "[dev-defaults] Dev defaults provisioned successfully"
SCRIPT
    chmod 755 ${D}${bindir}/monitor-dev-defaults.sh
}

SYSTEMD_SERVICE:${PN} = "monitor-dev-defaults.service"

FILES:${PN} = " \
    ${sysconfdir}/systemd/system/monitor.service.d/10-dev-logging.conf \
    ${sysconfdir}/systemd/system/camera-streamer.service.d/10-dev-logging.conf \
    ${sysconfdir}/systemd/system/monitor-dev-defaults.service \
    ${bindir}/monitor-dev-defaults.sh \
    "

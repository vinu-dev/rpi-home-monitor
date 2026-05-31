#!/bin/sh
# REQ: SWR-010, SWR-046; RISK: RISK-004, RISK-019; SEC: SC-003, SC-018; TEST: TC-013, TC-043
# =============================================================
# swupdate-check.sh — Post-boot health check for A/B OTA (ADR-0008)
#
# Runs after every boot. If an upgrade is pending (upgrade_available=1),
# validates that critical services are healthy. On success, confirms
# the update by clearing upgrade_available. On failure, requests another
# reboot so U-Boot can increment boot_count and roll back after bootlimit
# (3) failed attempts.
#
# Also runs resize2fs on the active rootfs partition to expand it
# to fill the 8 GB slot after a fresh OTA write.
# =============================================================
set -e

LOG_TAG="swupdate-check"
UPGRADE_AVAILABLE=$(fw_printenv -n upgrade_available 2>/dev/null || echo "0")

log() {
    logger -t "$LOG_TAG" "$1"
    echo "$LOG_TAG: $1"
}

mount_source() {
    target="$1"
    if command -v findmnt >/dev/null 2>&1; then
        found=$(findmnt -n -o SOURCE "$target" 2>/dev/null || true)
        if [ -n "$found" ]; then
            echo "$found"
            return 0
        fi
    fi
    awk -v target="$target" '$2 == target { print $1; exit }' /proc/mounts
}

cmdline_root_device() {
    for arg in $(cat /proc/cmdline 2>/dev/null); do
        case "$arg" in
            root=/dev/*)
                echo "${arg#root=}"
                return 0
                ;;
        esac
    done
}

split_partition_device() {
    PART_DISK=""
    PART_NUM=""
    dev="$1"
    case "$dev" in
        /dev/mmcblk*p[0-9]*)
            PART_DISK=$(echo "$dev" | sed 's/p[0-9][0-9]*$//')
            PART_NUM=$(echo "$dev" | sed 's/^.*p//')
            ;;
        /dev/[sv]d*[0-9]*)
            PART_DISK=$(echo "$dev" | sed 's/[0-9][0-9]*$//')
            PART_NUM=$(echo "$dev" | sed 's/^.*[^0-9]//')
            ;;
    esac
}

grow_partition_to_end() {
    disk="$1"
    partnum="$2"
    if [ -z "$disk" ] || [ -z "$partnum" ]; then
        return 0
    fi
    if command -v growpart >/dev/null 2>&1; then
        growpart "$disk" "$partnum" || true
    elif command -v parted >/dev/null 2>&1; then
        printf 'Yes\n' | parted ---pretend-input-tty "$disk" resizepart "$partnum" 100% || true
    fi
    partprobe "$disk" 2>/dev/null || true
    blockdev --rereadpt "$disk" 2>/dev/null || true
}

request_retry_reboot() {
    log "Requesting reboot so U-Boot can retry or roll back the failed OTA"
    sync || true
    if command -v systemctl >/dev/null 2>&1; then
        if systemctl --no-block reboot; then
            return 0
        fi
    fi
    reboot || true
}

# --- Always expand rootfs to fill partition (idempotent) ---
expand_rootfs() {
    ROOT_DEV=$(mount_source / 2>/dev/null || true)
    if [ "$ROOT_DEV" = "/dev/root" ] || [ -z "$ROOT_DEV" ]; then
        CMDLINE_ROOT=$(cmdline_root_device 2>/dev/null || true)
        if [ -n "$CMDLINE_ROOT" ]; then
            ROOT_DEV="$CMDLINE_ROOT"
        fi
    fi
    if [ -n "$ROOT_DEV" ]; then
        # resize2fs is a no-op if already at full size
        resize2fs "$ROOT_DEV" 2>/dev/null || true
    fi
}

# --- Always expand /data to fill the SD card tail (idempotent) ---
expand_data() {
    DATA_DEV=$(mount_source /data 2>/dev/null || true)
    if [ -z "$DATA_DEV" ]; then
        return 0
    fi

    GROW_DEV="$DATA_DEV"
    RESIZE_DEV="$DATA_DEV"
    if echo "$DATA_DEV" | grep -q '^/dev/mapper/' && command -v cryptsetup >/dev/null 2>&1; then
        MAP_NAME=$(basename "$DATA_DEV")
        BACKING_DEV=$(cryptsetup status "$MAP_NAME" 2>/dev/null | awk '/device:/ { print $2; exit }')
        if [ -n "$BACKING_DEV" ]; then
            GROW_DEV="$BACKING_DEV"
        fi
    fi

    split_partition_device "$GROW_DEV"
    if [ -n "$PART_DISK" ] && [ -n "$PART_NUM" ]; then
        grow_partition_to_end "$PART_DISK" "$PART_NUM"
    fi

    if echo "$DATA_DEV" | grep -q '^/dev/mapper/' && command -v cryptsetup >/dev/null 2>&1; then
        cryptsetup resize "$MAP_NAME" 2>/dev/null || true
    fi

    if command -v resize2fs >/dev/null 2>&1; then
        resize2fs "$RESIZE_DEV" 2>/dev/null || true
    fi
}

# --- Health checks ---
# Returns 0 if all checks pass, 1 if any fail.
# Checks are device-aware: server checks Flask+MediaMTX+NGINX,
# camera checks camera-streamer lifecycle.
# http_alive URL — returns 0 if anything responds on URL.
# A 401/403/404 is enough proof the app bound its port and is serving HTTP.
# Only "no HTTP response at all" (http_code 000) is a real liveness failure.
# Retries because monitor.service/camera-streamer.service are Type=simple:
# systemd marks them active as soon as python exec()s, long before Flask
# or http.server bind their sockets. Without retries we'd race them.
http_alive() {
    url=$1
    attempts=0
    while [ "$attempts" -lt 12 ]; do
        code=$(curl -sk -o /dev/null --max-time 5 -w '%{http_code}' "$url" 2>/dev/null || echo 000)
        if [ -n "$code" ] && [ "$code" != "000" ]; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 5
    done
    return 1
}

run_health_checks() {
    CHECKS_PASSED=0
    CHECKS_FAILED=0

    # Check 1: Is the main application service running?
    if systemctl is-active --quiet monitor.service 2>/dev/null; then
        log "CHECK OK: monitor.service is active"
        CHECKS_PASSED=$((CHECKS_PASSED + 1))

        # Server-specific checks
        # Check 2: Flask API alive (any HTTP response from :5000 counts —
        # the authenticated endpoints return 401 even when fully healthy).
        if http_alive http://127.0.0.1:5000/api/v1/ota/status; then
            log "CHECK OK: Flask API responding"
            CHECKS_PASSED=$((CHECKS_PASSED + 1))
        else
            log "CHECK FAIL: Flask API not responding"
            CHECKS_FAILED=$((CHECKS_FAILED + 1))
        fi

        # Check 3: MediaMTX process running
        if pgrep -x mediamtx >/dev/null 2>&1; then
            log "CHECK OK: mediamtx is running"
            CHECKS_PASSED=$((CHECKS_PASSED + 1))
        else
            log "CHECK WARN: mediamtx not running (may not be started yet)"
            # Not a hard failure — mediamtx may start after monitor
        fi

        # Check 4: NGINX responding
        if http_alive https://127.0.0.1:443/; then
            log "CHECK OK: NGINX responding on :443"
            CHECKS_PASSED=$((CHECKS_PASSED + 1))
        else
            log "CHECK WARN: NGINX not responding (may not be configured yet)"
            # Not a hard failure on first boot
        fi

    elif systemctl is-active --quiet camera-streamer.service 2>/dev/null; then
        log "CHECK OK: camera-streamer.service is active"
        CHECKS_PASSED=$((CHECKS_PASSED + 1))

        # Camera-specific checks
        # Check 2: Status server listens on :443 (serves the login/status
        # page). Port 8080 hosts the mTLS-only OTAAgent which would refuse
        # an unauthenticated probe — the wrong liveness target.
        if http_alive https://127.0.0.1:443/login; then
            log "CHECK OK: camera status server responding"
            CHECKS_PASSED=$((CHECKS_PASSED + 1))
        else
            log "CHECK FAIL: camera status server not responding"
            CHECKS_FAILED=$((CHECKS_FAILED + 1))
        fi
    else
        log "CHECK FAIL: no application service is active"
        CHECKS_FAILED=$((CHECKS_FAILED + 1))
    fi

    log "Health check results: ${CHECKS_PASSED} passed, ${CHECKS_FAILED} failed"

    if [ "$CHECKS_FAILED" -gt 0 ]; then
        return 1
    fi
    return 0
}

# --- Main ---
log "Starting post-boot check (upgrade_available=${UPGRADE_AVAILABLE})"

# Always expand rootfs
expand_rootfs
expand_data

if [ "$UPGRADE_AVAILABLE" != "1" ]; then
    log "No pending upgrade — nothing to confirm"
    exit 0
fi

# Hard overall deadline for the health check. Without this, a degraded
# service whose socket is half-open (accepts TCP but never responds)
# would tie up this script for many minutes — every http_alive retry
# stacks 12 × 5 s internally, and there are four probes. 240 s is
# enough for a healthy Pi Zero 2W boot (observed ~90 s) with headroom;
# anything longer should be treated as "this boot isn't coming up"
# and let U-Boot bootlimit do its job.
DEADLINE=$(( $(cut -d. -f1 /proc/uptime) + 240 ))
deadline_expired() {
    [ "$(cut -d. -f1 /proc/uptime)" -ge "$DEADLINE" ]
}

# Wait for services to start (up to 60 seconds)
log "Upgrade pending — waiting for services to start..."
WAIT=0
while [ $WAIT -lt 60 ]; do
    if deadline_expired; then
        log "Deadline hit while waiting for service to start"
        exit 1
    fi
    if systemctl is-active --quiet monitor.service 2>/dev/null || \
       systemctl is-active --quiet camera-streamer.service 2>/dev/null; then
        break
    fi
    sleep 5
    WAIT=$((WAIT + 5))
done

# Give services a moment to fully initialize
sleep 10

if deadline_expired; then
    log "Deadline hit before running health checks"
    exit 1
fi

if run_health_checks; then
    log "All health checks passed — confirming update"
    fw_setenv upgrade_available 0
    fw_setenv boot_count 0
    log "Update confirmed (upgrade_available=0, boot_count=0)"
else
    BOOT_COUNT=$(fw_printenv -n boot_count 2>/dev/null || echo "?")
    BOOTLIMIT=$(fw_printenv -n bootlimit 2>/dev/null || echo "3")
    log "Health checks FAILED — NOT confirming update (boot_count=${BOOT_COUNT}/${BOOTLIMIT})"
    log "System will reboot and rollback after ${BOOTLIMIT} failed boot attempts"
    request_retry_reboot
    exit 1
fi

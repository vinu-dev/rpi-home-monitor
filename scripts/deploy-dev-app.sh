#!/usr/bin/env bash
# REQ: SWR-048; RISK: RISK-022; SEC: SC-018; TEST: TC-045, TC-047
# =============================================================================
# deploy-dev-app.sh - Safe app-only hot deploy for dev hardware
#
# Deploys the current server and/or camera application tree to a live dev
# device without rebuilding or reflashing the full Yocto image.
#
# Usage:
#   ./scripts/deploy-dev-app.sh --server <ip>
#   ./scripts/deploy-dev-app.sh --camera <ip>
#   ./scripts/deploy-dev-app.sh --server <ip> --camera <ip>
#
# Optional:
#   --server-user <user>   SSH user for server (default: root)
#   --camera-user <user>   SSH user for camera (default: root)
#   --skip-restart         Copy files only, do not restart services
#
# Requirements:
#   - bash
#   - ssh
#   - scp
#   - tar
#
# This is a dev/lab workflow only. It does not replace the signed OTA path.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_IP=""
CAMERA_IP=""
SERVER_USER="root"
CAMERA_USER="root"
SKIP_RESTART=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SERVER_STAGE="/tmp/codex-deploy-server"
CAMERA_STAGE="/tmp/codex-deploy-camera"
SSH_OPTS=(
    -o StrictHostKeyChecking=accept-new
    -o ConnectTimeout=10
)

usage() {
    cat <<EOF
Usage: $0 [options]

Required:
  --server <ip>           Deploy server app to the given host
  --camera <ip>           Deploy camera app to the given host

Optional:
  --server-user <user>    SSH user for server (default: root)
  --camera-user <user>    SSH user for camera (default: root)
  --skip-restart          Copy files only, do not restart services
  -h, --help              Show this help

Examples:
  $0 --server <server-ip>
  $0 --camera <camera-ip>
  $0 --server <server-ip> --camera <camera-ip>
EOF
}

log() {
    echo -e "${BLUE}==>${NC} $1"
}

pass() {
    echo -e "${GREEN}PASS${NC} $1"
}

fail() {
    echo -e "${RED}FAIL${NC} $1"
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "Missing required command: $1"
        exit 1
    fi
}

local_scp_path() {
    local path="$1"
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$path"
    else
        printf '%s\n' "$path"
    fi
}

check_http_status() {
    local url="$1"
    local expected_a="$2"
    local expected_b="${3:-}"
    local status

    status="$(curl -sk -o /dev/null -w "%{http_code}" --connect-timeout 10 --max-time 15 "$url" 2>/dev/null || true)"
    if [ "$status" = "$expected_a" ] || { [ -n "$expected_b" ] && [ "$status" = "$expected_b" ]; }; then
        pass "$url returned HTTP $status"
    else
        fail "$url returned HTTP ${status:-000} (expected $expected_a${expected_b:+ or $expected_b})"
        exit 1
    fi
}

wait_for_http_status() {
    local url="$1"
    local expected_a="$2"
    local expected_b="${3:-}"
    local timeout="${4:-30}"
    local elapsed=0
    local status=""

    while [ "$elapsed" -lt "$timeout" ]; do
        status="$(curl -sk -o /dev/null -w "%{http_code}" --connect-timeout 10 --max-time 15 "$url" 2>/dev/null || true)"
        if [ "$status" = "$expected_a" ] || { [ -n "$expected_b" ] && [ "$status" = "$expected_b" ]; }; then
            pass "$url returned HTTP $status after ${elapsed}s"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    fail "$url returned HTTP ${status:-000} after ${timeout}s (expected $expected_a${expected_b:+ or $expected_b})"
    exit 1
}

remote_mkdir_clean() {
    local host="$1"
    local stage="$2"
    ssh "${SSH_OPTS[@]}" "$host" "rm -rf '$stage' && mkdir -p '$stage'"
}

copy_tree() {
    local src_dir="$1"
    local host="$2"
    local stage="$3"
    local base_name="$4"

    scp "${SSH_OPTS[@]}" -r "$(local_scp_path "$src_dir")" "${host}:${stage}/"
    if ! ssh "${SSH_OPTS[@]}" "$host" "test -d '$stage/$base_name'"; then
        fail "Remote copy missing expected directory: $stage/$base_name"
        exit 1
    fi
}

copy_file() {
    local src_file="$1"
    local host="$2"
    local stage="$3"
    scp "${SSH_OPTS[@]}" "$(local_scp_path "$src_file")" "${host}:${stage}/"
}

deploy_server() {
    local host="${SERVER_USER}@${SERVER_IP}"

    log "Preparing server staging area on ${SERVER_IP}"
    remote_mkdir_clean "$host" "$SERVER_STAGE"

    log "Copying server app files"
    copy_tree "$REPO_ROOT/app/server/monitor" "$host" "$SERVER_STAGE" "monitor"
    copy_file "$REPO_ROOT/app/server/setup.py" "$host" "$SERVER_STAGE"
    copy_file "$REPO_ROOT/app/server/requirements.txt" "$host" "$SERVER_STAGE"
    copy_file "$REPO_ROOT/app/server/config/nginx-monitor.conf" "$host" "$SERVER_STAGE"
    copy_file "$REPO_ROOT/app/server/config/monitor.service" "$host" "$SERVER_STAGE"
    copy_file "$REPO_ROOT/app/server/config/monitor-privileged-helper.service" "$host" "$SERVER_STAGE"
    copy_file "$REPO_ROOT/app/server/config/monitor-hotspot.sh" "$host" "$SERVER_STAGE"
    copy_file "$REPO_ROOT/app/server/config/monitor-hotspot.service" "$host" "$SERVER_STAGE"
    copy_file "$REPO_ROOT/app/server/config/gpio-trigger.sh" "$host" "$SERVER_STAGE"
    copy_file "$REPO_ROOT/app/server/config/gpio-trigger.service" "$host" "$SERVER_STAGE"

    log "Installing server app into /opt/monitor"
    ssh "${SSH_OPTS[@]}" "$host" "
        set -e
        rm -rf /opt/monitor/monitor_old
        if [ -d /opt/monitor/monitor ]; then
            cp -a /opt/monitor/monitor /opt/monitor/monitor_old
        fi
        rm -rf /opt/monitor/monitor
        mv '$SERVER_STAGE/monitor' /opt/monitor/monitor
        cp '$SERVER_STAGE/setup.py' /opt/monitor/setup.py
        cp '$SERVER_STAGE/requirements.txt' /opt/monitor/requirements.txt
        chown -R root:root /opt/monitor/monitor /opt/monitor/setup.py /opt/monitor/requirements.txt
        find /opt/monitor/monitor -type d -exec chmod 755 {} \;
        find /opt/monitor/monitor -type f -exec chmod 644 {} \;
        chmod 0644 /opt/monitor/setup.py /opt/monitor/requirements.txt
        # Install/upgrade Python dependencies (e.g. zeroconf for mDNS browsing)
        pip3 install -q -r /opt/monitor/requirements.txt
        # Pre-compile bytecode so first-request import is instant
        python3 -m compileall -q /opt/monitor/monitor
        # Deploy updated nginx config and reload (non-fatal if nginx is not running)
        if [ -f '$SERVER_STAGE/nginx-monitor.conf' ]; then
            cp '$SERVER_STAGE/nginx-monitor.conf' /etc/nginx/sites-enabled/monitor.conf
            nginx -t 2>/dev/null && nginx -s reload 2>/dev/null || true
        fi
        id -u monitor >/dev/null 2>&1 || useradd -r -d /opt/monitor -s /bin/false -U monitor
        mkdir -p /data/config /data/recordings /data/live /data/logs /data/certs
        chown -R monitor:monitor /data/config /data/recordings /data/live /data/logs /data/certs
        mkdir -p /opt/scripts
        mkdir -p /opt/monitor/scripts
        cp '$SERVER_STAGE/monitor-hotspot.sh' /opt/monitor/scripts/monitor-hotspot.sh
        chmod 0755 /opt/monitor/scripts/monitor-hotspot.sh
        cp '$SERVER_STAGE/gpio-trigger.sh' /opt/scripts/gpio-trigger.sh
        chmod 0755 /opt/scripts/gpio-trigger.sh
        cp '$SERVER_STAGE/monitor.service' /etc/systemd/system/monitor.service
        cp '$SERVER_STAGE/monitor-privileged-helper.service' /etc/systemd/system/monitor-privileged-helper.service
        cp '$SERVER_STAGE/monitor-hotspot.service' /etc/systemd/system/monitor-hotspot.service
        cp '$SERVER_STAGE/gpio-trigger.service' /etc/systemd/system/gpio-trigger.service
    "

    log "Applying boot optimisation overrides"
    ssh "${SSH_OPTS[@]}" "$host" "
        # Full unit file override — /etc/systemd/system/ takes priority over /usr/lib/.
        # Removes network-online.target: monitor only needs localhost:5000, not internet.
        # systemd-networkd-wait-online times out ~90s on eth0 no-carrier (server is on
        # WiFi); NetworkManager-wait-online adds another ~60s. Total: ~2min wasted.
        cp '$SERVER_STAGE/monitor.service' /etc/systemd/system/monitor.service
        cp '$SERVER_STAGE/monitor-privileged-helper.service' /etc/systemd/system/monitor-privileged-helper.service
        # mediamtx: same — only listens on local RTSP port, no internet needed
        sed 's|After=network-online.target|After=network.target|;s|Wants=network-online.target||' \
            /usr/lib/systemd/system/mediamtx.service > /etc/systemd/system/mediamtx.service
        # Mask systemd-networkd-wait-online: always times out on eth0 no-carrier
        systemctl mask systemd-networkd-wait-online.service 2>/dev/null || true
        systemctl daemon-reload
        systemctl enable gpio-trigger.service monitor-hotspot.service monitor-privileged-helper.service monitor.service >/dev/null 2>&1 || true
    "

    if [ "$SKIP_RESTART" -eq 0 ]; then
        log "Restarting server services"
        ssh "${SSH_OPTS[@]}" "$host" "systemctl restart monitor-privileged-helper monitor nginx && systemctl is-active monitor-privileged-helper monitor nginx >/dev/null"
    fi

    log "Validating server health"
    wait_for_http_status "https://${SERVER_IP}/login" "200" "" "45"
    wait_for_http_status "https://${SERVER_IP}/static/css/style.css" "200" "" "20"
    ssh "${SSH_OPTS[@]}" "$host" "systemctl is-active monitor-privileged-helper monitor nginx mediamtx"

    log "Cleaning server staging area"
    ssh "${SSH_OPTS[@]}" "$host" "rm -rf '$SERVER_STAGE'"
    pass "Server deploy completed on ${SERVER_IP}"
}

deploy_camera() {
    local host="${CAMERA_USER}@${CAMERA_IP}"

    log "Preparing camera staging area on ${CAMERA_IP}"
    remote_mkdir_clean "$host" "$CAMERA_STAGE"

    log "Copying camera app files"
    copy_tree "$REPO_ROOT/app/camera/camera_streamer" "$host" "$CAMERA_STAGE" "camera_streamer"
    copy_file "$REPO_ROOT/app/camera/setup.py" "$host" "$CAMERA_STAGE"
    copy_file "$REPO_ROOT/app/camera/requirements.txt" "$host" "$CAMERA_STAGE"
    copy_file "$REPO_ROOT/app/camera/config/camera.conf.default" "$host" "$CAMERA_STAGE"
    copy_file "$REPO_ROOT/app/camera/config/camera-streamer.service" "$host" "$CAMERA_STAGE"
    copy_file "$REPO_ROOT/app/camera/config/camera-privileged-helper.service" "$host" "$CAMERA_STAGE"
    copy_file "$REPO_ROOT/app/camera/config/camera-hotspot.sh" "$host" "$CAMERA_STAGE"
    copy_file "$REPO_ROOT/app/camera/config/camera-hotspot.service" "$host" "$CAMERA_STAGE"
    copy_file "$REPO_ROOT/app/server/config/gpio-trigger.sh" "$host" "$CAMERA_STAGE"
    copy_file "$REPO_ROOT/app/server/config/gpio-trigger.service" "$host" "$CAMERA_STAGE"

    log "Installing camera app into /opt/camera"
    ssh "${SSH_OPTS[@]}" "$host" "
        set -e
        rm -rf /opt/camera/camera_streamer_old
        if [ -d /opt/camera/camera_streamer ]; then
            cp -a /opt/camera/camera_streamer /opt/camera/camera_streamer_old
        fi
        rm -rf /opt/camera/camera_streamer
        mv '$CAMERA_STAGE/camera_streamer' /opt/camera/camera_streamer
        cp '$CAMERA_STAGE/setup.py' /opt/camera/setup.py
        cp '$CAMERA_STAGE/requirements.txt' /opt/camera/requirements.txt
        cp '$CAMERA_STAGE/camera.conf.default' /opt/camera/camera.conf.default
        chown -R camera:camera /opt/camera/camera_streamer
        chown root:root /opt/camera/setup.py /opt/camera/requirements.txt /opt/camera/camera.conf.default
        find /opt/camera/camera_streamer -type d -exec chmod 755 {} \;
        find /opt/camera/camera_streamer -type f -exec chmod 644 {} \;
        chmod 0644 /opt/camera/setup.py /opt/camera/requirements.txt /opt/camera/camera.conf.default
        # Pre-compile bytecode so first-request import is instant
        python3 -m compileall -q /opt/camera/camera_streamer
        chown -R camera:camera /opt/camera/camera_streamer/__pycache__ 2>/dev/null || true
        mkdir -p /opt/scripts
        mkdir -p /opt/camera/scripts
        cp '$CAMERA_STAGE/camera-hotspot.sh' /opt/camera/scripts/camera-hotspot.sh
        chmod 0755 /opt/camera/scripts/camera-hotspot.sh
        cp '$CAMERA_STAGE/gpio-trigger.sh' /opt/scripts/gpio-trigger.sh
        chmod 0755 /opt/scripts/gpio-trigger.sh
        cp '$CAMERA_STAGE/gpio-trigger.service' /etc/systemd/system/gpio-trigger.service
    "

    log "Applying boot optimisation overrides"
    ssh "${SSH_OPTS[@]}" "$host" "
        # Full unit file override from the repo. Keep this in sync with
        # app/camera/config/camera-streamer.service so hardening exceptions
        # such as /var/lib/camera-ota cannot drift in the deploy path.
        cp '$CAMERA_STAGE/camera-streamer.service' /etc/systemd/system/camera-streamer.service
        cp '$CAMERA_STAGE/camera-privileged-helper.service' /etc/systemd/system/camera-privileged-helper.service
        cp '$CAMERA_STAGE/camera-hotspot.service' /etc/systemd/system/camera-hotspot.service
        # journald limits — prevent /run from filling up under active streaming.
        # Without explicit RuntimeMaxUse, journald's implicit cap (~7MB on a 70MB
        # /run) is not enforced tightly enough under the ~6 entries/sec picamera2
        # log flood, causing archived journals to accumulate and daemon-reload to
        # fail when /run drops below the 16MB safety buffer. See issue #170.
        mkdir -p /etc/systemd/journald.conf.d
        cat > /etc/systemd/journald.conf.d/10-camera-limits.conf << 'EOF'
[Journal]
Storage=volatile
RuntimeMaxUse=8M
RuntimeKeepFree=20M
RuntimeMaxFileSize=2M
EOF
        # Tailscale: skip if unconfigured — saves ~50MB RAM on Zero 2W
        state_keys=0
        if [ -f /data/tailscale/tailscaled.state ]; then
            state_keys=\$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' /data/tailscale/tailscaled.state 2>/dev/null || echo 0)
        fi
        if [ \"\$state_keys\" = '0' ]; then
            systemctl stop tailscaled 2>/dev/null || true
            mkdir -p /etc/systemd/system/tailscaled.service.d
            cat > /etc/systemd/system/tailscaled.service.d/50-require-config.conf << 'EOF'
[Unit]
ConditionPathExists=/data/tailscale/tailscaled.state
ConditionFileNotEmpty=/data/tailscale/tailscaled.state
EOF
        fi
        systemctl daemon-reload
        systemctl enable gpio-trigger.service camera-privileged-helper.service camera-hotspot.service camera-streamer.service >/dev/null 2>&1 || true
    "

    if [ "$SKIP_RESTART" -eq 0 ]; then
        log "Restarting camera service"
        ssh "${SSH_OPTS[@]}" "$host" "systemctl restart camera-privileged-helper camera-streamer && systemctl is-active camera-privileged-helper camera-streamer >/dev/null"
    fi

    log "Validating camera health"
    wait_for_http_status "https://${CAMERA_IP}/" "302" "200" "45"
    wait_for_http_status "https://${CAMERA_IP}/login" "200" "" "20"
    ssh "${SSH_OPTS[@]}" "$host" "systemctl is-active camera-privileged-helper camera-streamer avahi-daemon"

    log "Cleaning camera staging area"
    ssh "${SSH_OPTS[@]}" "$host" "rm -rf '$CAMERA_STAGE'"
    pass "Camera deploy completed on ${CAMERA_IP}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --server)
            SERVER_IP="${2:-}"
            shift 2
            ;;
        --camera)
            CAMERA_IP="${2:-}"
            shift 2
            ;;
        --server-user)
            SERVER_USER="${2:-}"
            shift 2
            ;;
        --camera-user)
            CAMERA_USER="${2:-}"
            shift 2
            ;;
        --skip-restart)
            SKIP_RESTART=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

if [ -z "$SERVER_IP" ] && [ -z "$CAMERA_IP" ]; then
    usage
    exit 1
fi

require_cmd ssh
require_cmd scp
require_cmd curl

if [ -n "$SERVER_IP" ]; then
    deploy_server
fi

if [ -n "$CAMERA_IP" ]; then
    deploy_camera
fi

pass "Dev app deploy workflow finished"

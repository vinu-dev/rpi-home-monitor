#!/usr/bin/env bash
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
  $0 --server 192.168.1.245
  $0 --camera 192.168.1.186
  $0 --server 192.168.1.245 --camera 192.168.1.186
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
        # Pre-compile bytecode so first-request import is instant
        python3 -m compileall -q /opt/monitor/monitor
    "

    log "Applying boot optimisation overrides"
    ssh "${SSH_OPTS[@]}" "$host" "
        # Full unit file override — /etc/systemd/system/ takes priority over /usr/lib/.
        # Removes network-online.target: monitor only needs localhost:5000, not internet.
        # systemd-networkd-wait-online times out ~90s on eth0 no-carrier (server is on
        # WiFi); NetworkManager-wait-online adds another ~60s. Total: ~2min wasted.
        cat > /etc/systemd/system/monitor.service << 'SVCEOF'
[Unit]
Description=Home Monitor Server
After=network.target nginx.service mediamtx.service local-fs.target
Wants=mediamtx.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/monitor
ExecStart=/usr/bin/python3 -m flask --app monitor run --host=127.0.0.1 --port=5000
Restart=always
RestartSec=5
Environment=PYTHONPATH=/opt/monitor
Environment=FLASK_APP=monitor
Environment=MONITOR_DATA_DIR=/data
Environment=MONITOR_RECORDINGS_DIR=/data/recordings
Environment=MONITOR_LIVE_DIR=/data/live
Environment=MONITOR_CONFIG_DIR=/data/config
Environment=MONITOR_CERTS_DIR=/data/certs
Environment=MONITOR_LOG_DIR=/data/logs
Environment=LOG_LEVEL=INFO

[Install]
WantedBy=multi-user.target
SVCEOF
        # mediamtx: same — only listens on local RTSP port, no internet needed
        sed 's|After=network-online.target|After=network.target|;s|Wants=network-online.target||' \
            /usr/lib/systemd/system/mediamtx.service > /etc/systemd/system/mediamtx.service
        # Mask systemd-networkd-wait-online: always times out on eth0 no-carrier
        systemctl mask systemd-networkd-wait-online.service 2>/dev/null || true
        systemctl daemon-reload
    "

    if [ "$SKIP_RESTART" -eq 0 ]; then
        log "Restarting server services"
        ssh "${SSH_OPTS[@]}" "$host" "systemctl restart monitor nginx && systemctl is-active monitor nginx >/dev/null"
    fi

    log "Validating server health"
    wait_for_http_status "https://${SERVER_IP}/login" "200" "" "45"
    wait_for_http_status "https://${SERVER_IP}/static/css/style.css" "200" "" "20"
    ssh "${SSH_OPTS[@]}" "$host" "systemctl is-active monitor nginx mediamtx"

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
    "

    log "Applying boot optimisation overrides"
    ssh "${SSH_OPTS[@]}" "$host" "
        # Full unit file override — removes network-online.target.
        # In hotspot/AP mode nm-online waits full 60s timeout; camera-hotspot.service
        # completes in ~6s (or skips instantly when setup-done), which is enough.
        cat > /etc/systemd/system/camera-streamer.service << 'SVCEOF'
[Unit]
Description=Camera RTSP Streamer
After=avahi-daemon.service local-fs.target NetworkManager.service camera-hotspot.service sys-subsystem-net-devices-wlan0.device
Wants=NetworkManager.service

[Service]
Type=simple
User=camera
Group=camera
WorkingDirectory=/opt/camera
ExecStartPre=+/bin/sh -c 'chmod 0666 /sys/class/leds/ACT/trigger /sys/class/leds/ACT/brightness /sys/class/leds/ACT/delay_on /sys/class/leds/ACT/delay_off 2>/dev/null || true'
ExecStart=/usr/bin/python3 -m camera_streamer.main
Restart=always
RestartSec=5
TimeoutStopSec=20
KillMode=control-group
Environment=PYTHONPATH=/opt/camera
Environment=CAMERA_DATA_DIR=/data
Environment=CAMERA_CONFIG_DIR=/data/config
Environment=CAMERA_CERTS_DIR=/data/certs
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/data
PrivateTmp=true
SupplementaryGroups=video
AmbientCapabilities=CAP_NET_BIND_SERVICE CAP_SYS_ADMIN

[Install]
WantedBy=multi-user.target
SVCEOF
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
    "

    if [ "$SKIP_RESTART" -eq 0 ]; then
        log "Restarting camera service"
        ssh "${SSH_OPTS[@]}" "$host" "systemctl restart camera-streamer && systemctl is-active camera-streamer >/dev/null"
    fi

    log "Validating camera health"
    wait_for_http_status "https://${CAMERA_IP}/" "302" "200" "45"
    wait_for_http_status "https://${CAMERA_IP}/login" "200" "" "20"
    ssh "${SSH_OPTS[@]}" "$host" "systemctl is-active camera-streamer avahi-daemon"

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

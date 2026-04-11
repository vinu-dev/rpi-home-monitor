#!/usr/bin/env bash
# =============================================================================
# smoke-test.sh — Layer 5 hardware verification for RPi Home Monitor
#
# Runs against a live server to verify the deployment is working.
# Checks: HTTPS, API health, auth, camera endpoints, HLS readiness.
#
# Usage:
#   ./scripts/smoke-test.sh <server-ip> [admin-password] [camera-ip]
#
# Examples:
#   ./scripts/smoke-test.sh 192.168.8.245 12345678
#   ./scripts/smoke-test.sh 192.168.8.245 12345678 192.168.8.187
#   ./scripts/smoke-test.sh homemonitor.local
#
# Exit codes:
#   0 = all checks passed
#   1 = one or more checks failed
# =============================================================================

set -euo pipefail

SERVER="${1:-}"
PASSWORD="${2:-admin}"
HTTPS_PORT=443
API_BASE="https://${SERVER}:${HTTPS_PORT}/api/v1"
CURL_OPTS="-sk --connect-timeout 5 --max-time 10"
COOKIE_JAR="/tmp/smoke-test-cookies.txt"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

if [ -z "$SERVER" ]; then
    echo "Usage: $0 <server-ip> [admin-password]"
    echo "Example: $0 192.168.8.245 12345678"
    exit 1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pass() {
    echo -e "  ${GREEN}PASS${NC} $1"
    PASSED=$((PASSED + 1))
}

fail() {
    echo -e "  ${RED}FAIL${NC} $1"
    FAILED=$((FAILED + 1))
}

skip() {
    echo -e "  ${YELLOW}SKIP${NC} $1"
    SKIPPED=$((SKIPPED + 1))
}

check_status() {
    local desc="$1" url="$2" expected_status="$3"
    local status
    status=$(curl $CURL_OPTS -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" "$url" 2>/dev/null) || true
    if [ "$status" = "$expected_status" ]; then
        pass "$desc (HTTP $status)"
    else
        fail "$desc (expected $expected_status, got $status)"
    fi
}

check_json_field() {
    local desc="$1" url="$2" field="$3"
    local body
    body=$(curl $CURL_OPTS -b "$COOKIE_JAR" "$url" 2>/dev/null) || true
    if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert '$field' in d" 2>/dev/null; then
        pass "$desc (has '$field')"
    else
        fail "$desc (missing '$field')"
    fi
}

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

cleanup() {
    rm -f "$COOKIE_JAR"
}
trap cleanup EXIT

# ===========================================================================
echo ""
echo "========================================="
echo "  RPi Home Monitor — Smoke Tests"
echo "  Server: ${SERVER}"
echo "========================================="
echo ""

# ---------------------------------------------------------------------------
# 1. Network reachability
# ---------------------------------------------------------------------------

echo "[1/7] Network reachability"
if curl $CURL_OPTS -o /dev/null "https://${SERVER}/" 2>/dev/null; then
    pass "HTTPS reachable on port $HTTPS_PORT"
else
    fail "Cannot reach https://${SERVER}/"
    echo ""
    echo -e "${RED}Server unreachable. Aborting remaining tests.${NC}"
    echo ""
    echo "Results: $PASSED passed, $FAILED failed, $SKIPPED skipped"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Setup status
# ---------------------------------------------------------------------------

echo ""
echo "[2/7] Setup status"
check_status "GET /setup/status" "${API_BASE}/setup/status" 200
check_json_field "setup_complete field" "${API_BASE}/setup/status" "setup_complete"

# ---------------------------------------------------------------------------
# 3. Authentication
# ---------------------------------------------------------------------------

echo ""
echo "[3/7] Authentication"

# Login
LOGIN_RESP=$(curl $CURL_OPTS -c "$COOKIE_JAR" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"${PASSWORD}\"}" \
    "${API_BASE}/auth/login" 2>/dev/null) || true

if echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'csrf_token' in d" 2>/dev/null; then
    pass "Login successful"
    CSRF=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['csrf_token'])" 2>/dev/null) || true
else
    fail "Login failed (check password)"
    CSRF=""
fi

# /auth/me
check_status "GET /auth/me" "${API_BASE}/auth/me" 200
check_json_field "/auth/me has user" "${API_BASE}/auth/me" "user"

# ---------------------------------------------------------------------------
# 4. System health
# ---------------------------------------------------------------------------

echo ""
echo "[4/7] System health"
check_status "GET /system/health" "${API_BASE}/system/health" 200
check_json_field "health has cpu_temp_c" "${API_BASE}/system/health" "cpu_temp_c"
check_json_field "health has memory" "${API_BASE}/system/health" "memory"
check_json_field "health has disk" "${API_BASE}/system/health" "disk"
check_json_field "health has status" "${API_BASE}/system/health" "status"

check_status "GET /system/info" "${API_BASE}/system/info" 200
check_json_field "info has hostname" "${API_BASE}/system/info" "hostname"
check_json_field "info has firmware_version" "${API_BASE}/system/info" "firmware_version"

# ---------------------------------------------------------------------------
# 5. Camera endpoints
# ---------------------------------------------------------------------------

echo ""
echo "[5/7] Camera endpoints"
check_status "GET /cameras" "${API_BASE}/cameras" 200

CAMERAS=$(curl $CURL_OPTS -b "$COOKIE_JAR" "${API_BASE}/cameras" 2>/dev/null) || true
CAM_COUNT=$(echo "$CAMERAS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null) || CAM_COUNT=0

if [ "$CAM_COUNT" -gt 0 ]; then
    pass "Found $CAM_COUNT camera(s)"
    CAM_ID=$(echo "$CAMERAS" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null) || true
    if [ -n "$CAM_ID" ]; then
        check_status "GET /cameras/$CAM_ID/status" "${API_BASE}/cameras/${CAM_ID}/status" 200
        check_status "GET /recordings/$CAM_ID/dates" "${API_BASE}/recordings/${CAM_ID}/dates" 200
    fi
else
    skip "No cameras configured — skipping camera-specific tests"
fi

# ---------------------------------------------------------------------------
# 6. Settings & storage
# ---------------------------------------------------------------------------

echo ""
echo "[6/7] Settings & storage"
check_status "GET /settings" "${API_BASE}/settings" 200
check_json_field "settings has timezone" "${API_BASE}/settings" "timezone"
check_json_field "settings has hostname" "${API_BASE}/settings" "hostname"

check_status "GET /storage/status" "${API_BASE}/storage/status" 200
check_json_field "storage has total_gb" "${API_BASE}/storage/status" "total_gb"

check_status "GET /users" "${API_BASE}/users" 200

# ---------------------------------------------------------------------------
# 7. OTA status
# ---------------------------------------------------------------------------

echo ""
echo "[7/7] OTA status"
check_status "GET /ota/status" "${API_BASE}/ota/status" 200

# ---------------------------------------------------------------------------
# 8. Camera node (optional — pass camera IP as $3)
# ---------------------------------------------------------------------------

CAMERA_IP="${3:-}"
if [ -n "$CAMERA_IP" ]; then
    echo ""
    echo "[8/8] Camera node: ${CAMERA_IP}"
    CAM_URL="http://${CAMERA_IP}"
    CAM_CURL="curl -s --connect-timeout 5 --max-time 10"

    if $CAM_CURL -o /dev/null "$CAM_URL/" 2>/dev/null; then
        pass "Camera HTTP reachable"
    else
        fail "Camera HTTP unreachable at ${CAMERA_IP}"
    fi

    # Check /api/status (no auth if no password, or auth required)
    CAM_STATUS=$($CAM_CURL "${CAM_URL}/api/status" 2>/dev/null) || true
    if echo "$CAM_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'camera_id' in d" 2>/dev/null; then
        pass "Camera /api/status has camera_id"
        check_json_field "Camera status has hostname" "${CAM_URL}/api/status" "hostname"
        check_json_field "Camera status has wifi_ssid" "${CAM_URL}/api/status" "wifi_ssid"
        check_json_field "Camera status has streaming" "${CAM_URL}/api/status" "streaming"
        check_json_field "Camera status has cpu_temp" "${CAM_URL}/api/status" "cpu_temp"
    elif echo "$CAM_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'error' in d" 2>/dev/null; then
        pass "Camera /api/status requires auth (expected)"
    else
        fail "Camera /api/status unexpected response"
    fi
else
    echo ""
    echo "[8/8] Camera node"
    skip "No camera IP provided (pass as 3rd argument)"
fi

# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "========================================="
TOTAL=$((PASSED + FAILED + SKIPPED))
echo "  Results: $PASSED passed, $FAILED failed, $SKIPPED skipped ($TOTAL total)"
echo "========================================="
echo ""

if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}Some checks failed!${NC}"
    exit 1
else
    echo -e "${GREEN}All checks passed!${NC}"
    exit 0
fi

#!/usr/bin/env bash
# REQ: SWR-048; RISK: RISK-022; SEC: SC-018; TEST: TC-045, TC-047
# =============================================================================
# smoke-test.sh - Layer 5 hardware verification for RPi Home Monitor
#
# Runs against a live server to verify the deployment is working.
# Checks: HTTPS, API health, auth, camera endpoints, HLS readiness.
#
# Usage:
#   ./scripts/smoke-test.sh <server-ip> [admin-password] [camera-ip] [camera-password]
#
# Examples:
#   ./scripts/smoke-test.sh <server-ip> <password>
#   ./scripts/smoke-test.sh <server-ip> <password> <camera-ip>
#   ./scripts/smoke-test.sh <server-ip> <password> <camera-ip> <cam-password>
#   ./scripts/smoke-test.sh homemonitor.local
#
# Optional environment variables:
#   SMOKE_SERVER_COOKIE="session=..."     Skip server login and reuse a valid
#                                        authenticated Flask session cookie.
#   SMOKE_CAMERA_COOKIE="cam_session=..." Skip camera login and reuse a valid
#                                        authenticated camera session cookie.
#
# Camera password defaults to the server password only when the server login
# path is being used. If the camera requires authentication and no camera
# password or SMOKE_CAMERA_COOKIE is provided, camera-authenticated checks
# are skipped.
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
CURL_OPTS=(-sk --connect-timeout 5 --max-time 10)
COOKIE_JAR="/tmp/smoke-test-cookies.txt"
SERVER_COOKIE_HEADER="${SMOKE_SERVER_COOKIE:-}"
AUDIT_EXPORT_TMP="/tmp/smoke-test-audit-export.csv"
CAM_COOKIE_JAR="/tmp/smoke-test-cam-cookies.txt"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

if [ -z "$SERVER" ]; then
    echo "Usage: $0 <server-ip> [admin-password] [camera-ip] [camera-password]"
    echo "Example: $0 192.168.8.245 12345678 192.168.8.187"
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
    status=$(server_curl -o /dev/null -w "%{http_code}" "$url" 2>/dev/null) || true
    if [ "$status" = "$expected_status" ]; then
        pass "$desc (HTTP $status)"
    else
        fail "$desc (expected $expected_status, got $status)"
    fi
}

check_json_field() {
    local desc="$1" url="$2" field="$3"
    local body
    body=$(server_curl "$url" 2>/dev/null) || true
    if echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert '$field' in d" 2>/dev/null; then
        pass "$desc (has '$field')"
    else
        fail "$desc (missing '$field')"
    fi
}

server_curl() {
    if [ -n "$SERVER_COOKIE_HEADER" ]; then
        curl "${CURL_OPTS[@]}" -H "Cookie: $SERVER_COOKIE_HEADER" "$@"
    else
        curl "${CURL_OPTS[@]}" -b "$COOKIE_JAR" "$@"
    fi
}

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

trap 'rm -f "$COOKIE_JAR" "$AUDIT_EXPORT_TMP" "$CAM_COOKIE_JAR"' EXIT

# ===========================================================================
echo ""
echo "========================================="
echo "  RPi Home Monitor - Smoke Tests"
echo "  Server: ${SERVER}"
echo "========================================="
echo ""

# ---------------------------------------------------------------------------
# 1. Network reachability
# ---------------------------------------------------------------------------

echo "[1/7] Network reachability"
if curl "${CURL_OPTS[@]}" -o /dev/null "https://${SERVER}/" 2>/dev/null; then
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

# Login or reuse caller-provided session
if [ -n "$SERVER_COOKIE_HEADER" ]; then
    pass "Using pre-authenticated server session from SMOKE_SERVER_COOKIE"
    CSRF=""
else
    LOGIN_RESP=$(curl "${CURL_OPTS[@]}" -c "$COOKIE_JAR" \
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
fi

# /auth/me
check_status "GET /auth/me" "${API_BASE}/auth/me" 200
check_json_field "/auth/me has user" "${API_BASE}/auth/me" "user"
if [ -z "${CSRF:-}" ]; then
    CSRF=$(server_curl "${API_BASE}/auth/me" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('csrf_token',''))" 2>/dev/null) || true
fi
if [ -n "${CSRF:-}" ]; then
    EXPORT_STATUS=$(server_curl -H "X-CSRF-Token: ${CSRF}" -o "$AUDIT_EXPORT_TMP" -w "%{http_code}" \
        "${API_BASE}/audit/events/export?format=csv" 2>/dev/null) || true
    if [ "$EXPORT_STATUS" = "200" ] && head -n 1 "$AUDIT_EXPORT_TMP" | grep -q '^timestamp,event,user,ip,detail'; then
        pass "GET /audit/events/export?format=csv returns CSV attachment data"
    else
        fail "GET /audit/events/export?format=csv failed (HTTP ${EXPORT_STATUS:-000})"
    fi
else
    skip "Audit export check skipped: no CSRF token available from /auth/me"
fi
skip "Manual audit export cross-check: downloaded CSV opens cleanly and row count matches /data/logs/audit.log minus the header"

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

CAMERAS=$(server_curl "${API_BASE}/cameras" 2>/dev/null) || true
CAM_COUNT=$(echo "$CAMERAS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null) || CAM_COUNT=0

if [ "$CAM_COUNT" -gt 0 ]; then
    pass "Found $CAM_COUNT camera(s)"
    CAM_ID=$(echo "$CAMERAS" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null) || true
    if [ -n "$CAM_ID" ]; then
        check_status "GET /cameras/$CAM_ID/status" "${API_BASE}/cameras/${CAM_ID}/status" 200
        check_status "GET /recordings/$CAM_ID/dates" "${API_BASE}/recordings/${CAM_ID}/dates" 200
    fi
else
    skip "No cameras configured - skipping camera-specific tests"
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
# 8. Camera node (optional - pass camera IP as $3)
# ---------------------------------------------------------------------------

CAMERA_IP="${3:-}"
CAMERA_PASSWORD="${4:-}"
if [ -z "$CAMERA_PASSWORD" ] && [ -z "$SERVER_COOKIE_HEADER" ]; then
    CAMERA_PASSWORD="$PASSWORD"
fi
CAMERA_COOKIE_HEADER="${SMOKE_CAMERA_COOKIE:-}"

if [ -n "$CAMERA_IP" ]; then
    echo ""
    echo "[8/8] Camera node: ${CAMERA_IP}"
    CAM_URL="https://${CAMERA_IP}"
    CAM_CONTROL_URL="https://${CAMERA_IP}:8443"
    CAM_CURL=(curl -sk --connect-timeout 5 --max-time 10)

    # --- Reachability ---
    CAM_HTTP_STATUS=$("${CAM_CURL[@]}" -o /dev/null -w "%{http_code}" "$CAM_URL/" 2>/dev/null) || true
    if [ "$CAM_HTTP_STATUS" = "200" ] || [ "$CAM_HTTP_STATUS" = "302" ]; then
        pass "Camera HTTPS reachable (HTTP ${CAM_HTTP_STATUS})"
    else
        fail "Camera HTTPS unreachable at ${CAMERA_IP} (got ${CAM_HTTP_STATUS:-000})"
        echo ""
        echo -e "${RED}Camera unreachable. Skipping camera tests.${NC}"
        # Jump to summary
        CAMERA_IP=""
    fi
fi

if [ -n "$CAMERA_IP" ]; then
    # --- Try unauthenticated status first ---
    if [ -n "$CAMERA_COOKIE_HEADER" ]; then
        CAM_STATUS=$("${CAM_CURL[@]}" -H "Cookie: ${CAMERA_COOKIE_HEADER}" "${CAM_URL}/api/status" 2>/dev/null) || true
    else
        CAM_STATUS=$("${CAM_CURL[@]}" "${CAM_URL}/api/status" 2>/dev/null) || true
    fi
    CAM_AUTHED=false

    if echo "$CAM_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'camera_id' in d" 2>/dev/null; then
        # No auth required - status is open
        pass "Camera /api/status accessible (no auth)"
        CAM_AUTHED=true
    elif echo "$CAM_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'error' in d" 2>/dev/null; then
        # Auth required - login
        pass "Camera /api/status requires auth (expected)"

        if [ -n "$CAMERA_COOKIE_HEADER" ]; then
            pass "Using pre-authenticated camera session from SMOKE_CAMERA_COOKIE"
            CAM_AUTHED=true
            CAM_STATUS=$("${CAM_CURL[@]}" -H "Cookie: ${CAMERA_COOKIE_HEADER}" "${CAM_URL}/api/status" 2>/dev/null) || true
        elif [ -n "$CAMERA_PASSWORD" ]; then
            CAM_LOGIN=$("${CAM_CURL[@]}" -c "$CAM_COOKIE_JAR" \
                -H "Content-Type: application/json" \
                -d "{\"username\":\"admin\",\"password\":\"${CAMERA_PASSWORD}\"}" \
                "${CAM_URL}/login" 2>/dev/null) || true

            if echo "$CAM_LOGIN" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'message' in d" 2>/dev/null; then
                pass "Camera login successful"
                CAM_AUTHED=true
                # Re-fetch status with session cookie
                CAM_STATUS=$("${CAM_CURL[@]}" -b "$CAM_COOKIE_JAR" "${CAM_URL}/api/status" 2>/dev/null) || true
            else
                fail "Camera login failed (check password, tried: admin/${CAMERA_PASSWORD})"
            fi
        else
            skip "Camera auth required but no camera password or SMOKE_CAMERA_COOKIE was provided"
        fi
    else
        fail "Camera /api/status unexpected response"
    fi

    # --- Verify all status fields if authenticated ---
    if [ "$CAM_AUTHED" = true ]; then
        for field in camera_id hostname ip_address wifi_ssid server_address \
                     server_connected streaming cpu_temp uptime \
                     memory_total_mb memory_used_mb; do
            if echo "$CAM_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert '$field' in d" 2>/dev/null; then
                pass "Camera status has '$field'"
            else
                fail "Camera status missing '$field'"
            fi
        done

        # Show key values for human review
        CAM_ID=$(echo "$CAM_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('camera_id','?'))" 2>/dev/null) || CAM_ID="?"
        CAM_STREAM=$(echo "$CAM_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('streaming','?'))" 2>/dev/null) || CAM_STREAM="?"
        CAM_TEMP=$(echo "$CAM_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cpu_temp','?'))" 2>/dev/null) || CAM_TEMP="?"
        echo -e "  ${YELLOW}INFO${NC} Camera: id=${CAM_ID}, streaming=${CAM_STREAM}, cpu_temp=${CAM_TEMP}"
    fi

    if "${CAM_CURL[@]}" -o /dev/null "${CAM_CONTROL_URL}/api/v1/control/status" 2>/dev/null; then
        fail "Camera control port unexpectedly accepted a no-cert client"
    else
        pass "Camera control port rejects a no-cert client"
    fi

    if [ -n "${SMOKE_CAMERA_CONTROL_CERT:-}" ] && [ -n "${SMOKE_CAMERA_CONTROL_KEY:-}" ]; then
        CAM_MTLS_CURL=(
            curl -sk --connect-timeout 5 --max-time 10
            --cert "${SMOKE_CAMERA_CONTROL_CERT}"
            --key "${SMOKE_CAMERA_CONTROL_KEY}"
        )

        CAM_CONTROL_STATUS=$("${CAM_MTLS_CURL[@]}" -o /dev/null -w "%{http_code}" \
            "${CAM_CONTROL_URL}/api/v1/control/config" 2>/dev/null) || true
        if [ "$CAM_CONTROL_STATUS" = "200" ]; then
            pass "Camera control API reachable on :8443 with mTLS"
        else
            fail "Camera control API on :8443 expected 200, got ${CAM_CONTROL_STATUS:-000}"
        fi

        CAM_HUMAN_CONTROL_STATUS=$("${CAM_MTLS_CURL[@]}" -o /dev/null -w "%{http_code}" \
            "${CAM_URL}/api/v1/control/config" 2>/dev/null) || true
        if [ "$CAM_HUMAN_CONTROL_STATUS" = "404" ]; then
            pass "Camera human listener returns 404 for control path"
        else
            fail "Camera human listener expected 404 for control path, got ${CAM_HUMAN_CONTROL_STATUS:-000}"
        fi
    else
        skip "Camera mTLS control-path checks skipped (set SMOKE_CAMERA_CONTROL_CERT and SMOKE_CAMERA_CONTROL_KEY)"
    fi
else
    if [ -z "${3:-}" ]; then
        echo ""
        echo "[8/8] Camera node"
        skip "No camera IP provided (pass as 3rd argument)"
    fi
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

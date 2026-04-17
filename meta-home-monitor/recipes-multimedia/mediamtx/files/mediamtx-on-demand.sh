#!/bin/sh
# MediaMTX on-demand hook for the Home Monitor (ADR-0017).
#
# Usage: mediamtx-on-demand.sh <camera_id> <start|stop>
#
# MediaMTX expands $MTX_PATH to the stream path (which we configure to be
# the camera id) when firing runOnDemand / runOnDemandCloseAfter. The server
# exposes a localhost-only blueprint at /internal/on-demand that consults
# the scheduler and forwards to the camera control channel.
#
# Idempotent: the server side handles "already running" / "still needed"
# cases. This script exits 0 on any 2xx response so MediaMTX logs stay clean.

set -eu

CAMERA_ID="${1:-${MTX_PATH:-}}"
ACTION="${2:-start}"

if [ -z "${CAMERA_ID}" ]; then
    echo "mediamtx-on-demand: missing camera id" >&2
    exit 1
fi

case "${ACTION}" in
    start|stop) ;;
    *)
        echo "mediamtx-on-demand: unknown action '${ACTION}'" >&2
        exit 1
        ;;
esac

URL="https://127.0.0.1/internal/on-demand/${CAMERA_ID}/${ACTION}"

# -f: fail on HTTP >=400, -s: silent, -S: show errors, -m: 5s max.
# -k: the server uses a self-signed cert; localhost-only traffic so the
# trade-off is acceptable here.
# We swallow curl's non-zero exit for transient cases so the MediaMTX
# supervisor doesn't bounce the path on a flapping network.
curl -fskS -m 5 -X POST -H 'Content-Type: application/json' --data '{}' "${URL}" || {
    echo "mediamtx-on-demand: ${ACTION} request failed for ${CAMERA_ID}" >&2
    exit 0
}

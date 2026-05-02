# REQ: SWR-004, SWR-026; RISK: RISK-005, RISK-015; SEC: SC-002; TEST: TC-005, TC-030
"""
Notify the paired server when camera stream config changes locally.

Uses HMAC-SHA256 signed requests with the pairing_secret for
authentication. Fire-and-forget — failures are logged but not retried.
The server's next dashboard poll will eventually sync regardless.

Part of the bidirectional config sync (ADR-0015).
"""

import hashlib
import hmac
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request

log = logging.getLogger("camera-streamer.server-notifier")

NOTIFY_TIMEOUT = 10  # seconds


def _build_signature(secret_hex, camera_id, timestamp, body_bytes):
    """Compute HMAC-SHA256(secret, camera_id:timestamp:sha256(body))."""
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    message = f"{camera_id}:{timestamp}:{body_hash}"
    return hmac.new(
        bytes.fromhex(secret_hex),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def _ssl_context(certs_dir):
    """Build SSL context with camera's client cert for TLS."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # server uses self-signed cert

    cert = os.path.join(certs_dir, "client.crt")
    key = os.path.join(certs_dir, "client.key")
    if os.path.isfile(cert) and os.path.isfile(key):
        ctx.load_cert_chain(cert, key)

    return ctx


def notify_config_change(config, pairing_manager):
    """POST current stream config to the paired server.

    Called from a daemon thread after a local config change succeeds.
    Args:
        config: ConfigManager instance (has server_ip, camera_id, certs_dir).
        pairing_manager: PairingManager instance (has get_pairing_secret()).
    """
    server_ip = config.server_ip
    if not server_ip:
        log.debug("No server IP configured, skipping config notification")
        return

    secret = pairing_manager.get_pairing_secret()
    if not secret:
        log.debug("No pairing secret, skipping config notification")
        return

    camera_id = config.camera_id
    timestamp = str(int(time.time()))

    stream_config = {
        "width": config.width,
        "height": config.height,
        "fps": config.fps,
        "bitrate": config.bitrate,
        "h264_profile": config.h264_profile,
        "keyframe_interval": config.keyframe_interval,
        "rotation": config.rotation,
        "hflip": config.hflip,
        "vflip": config.vflip,
    }

    body = json.dumps(stream_config).encode()
    signature = _build_signature(secret, camera_id, timestamp, body)

    url = f"https://{server_ip}/api/v1/cameras/config-notify"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Camera-ID": camera_id,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        },
    )

    try:
        ctx = _ssl_context(config.certs_dir)
        with urllib.request.urlopen(req, context=ctx, timeout=NOTIFY_TIMEOUT) as resp:
            log.info(
                "Config notification sent to server (%s): HTTP %d",
                server_ip,
                resp.status,
            )
    except urllib.error.HTTPError as e:
        log.warning("Config notification rejected by server: HTTP %d", e.code)
    except (urllib.error.URLError, OSError) as e:
        log.warning("Config notification failed (server %s): %s", server_ip, e)

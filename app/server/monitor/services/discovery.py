"""
Camera discovery service — tracks cameras via mDNS browsing and heartbeats.

Responsibilities:
- Browse for _rtsp._tcp services using python-zeroconf (RFC 6762/6763)
- Detect new cameras → add as pending (same path as heartbeat report_camera)
- Monitor paired cameras → update online/offline status
- Track camera firmware version from TXT records
- Trigger audit log entries for camera state changes

Camera considered offline after 30 seconds with no heartbeat (ADR-0016).

mDNS browser uses python-zeroconf, which is the industry standard library
(same as Home Assistant, Frigate). It continuously browses _rtsp._tcp.local.
and calls report_camera() for every discovered camera — the same code path
used by the heartbeat endpoint, so there is no separate discovery state.

DNS-SD / mDNS standards: RFC 6762 (Multicast DNS), RFC 6763 (DNS-SD).
Service type _rtsp._tcp is the standard for RTSP media sources.
"""

import logging
import socket
import threading
from datetime import UTC, datetime

log = logging.getLogger("monitor.discovery")

OFFLINE_TIMEOUT = 30  # seconds — must match _resume_camera_pipelines in __init__.py
_MDNS_SERVICE_TYPE = "_rtsp._tcp.local."
_CAMERA_ID_PREFIX = "cam-"


class DiscoveryService:
    """Manages camera discovery and status tracking.

    Combines two discovery paths into one unified report_camera() sink:
      1. mDNS browser — avahi-published _rtsp._tcp advertisements from cameras
      2. Self-registration — cameras POST /pair/register when not yet paired
      3. Heartbeat — paired cameras POST /cameras/heartbeat every 15s (ADR-0016)

    All three paths converge on report_camera(). No separate state is needed.
    """

    def __init__(self, store, audit=None):
        self._store = store
        self._audit = audit
        self._lock = threading.Lock()
        self._running = False

        # mDNS browser (started by start_mdns_browser, stopped by stop_mdns_browser)
        self._zeroconf = None
        self._mdns_browser = None

    # -------------------------------------------------------------------------
    # Core status tracking
    # -------------------------------------------------------------------------

    def report_camera(self, camera_id, ip, firmware_version=""):
        """Report a camera as seen (from mDNS, self-registration, or heartbeat).

        Creates a pending camera if unknown, or updates last_seen and status
        for known cameras. This is the single funnel for all discovery paths.
        """
        from monitor.models import Camera

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._lock:
            camera = self._store.get_camera(camera_id)
            if camera is None:
                # New camera discovered — add as pending (needs pairing)
                camera = Camera(
                    id=camera_id,
                    ip=ip,
                    status="pending",
                    last_seen=now,
                    firmware_version=firmware_version,
                )
                self._store.save_camera(camera)
                if self._audit:
                    self._audit.log_event(
                        "CAMERA_DISCOVERED",
                        detail=f"new camera {camera_id} at {ip}",
                    )
            else:
                # Known camera — update status and liveness timestamp
                was_offline = camera.status == "offline"
                camera.ip = ip
                camera.last_seen = now
                if firmware_version:
                    camera.firmware_version = firmware_version
                # Don't override "pending" status — it requires explicit pairing
                if camera.status not in ("pending",):
                    camera.status = "online"
                self._store.save_camera(camera)
                if was_offline and self._audit:
                    self._audit.log_event(
                        "CAMERA_ONLINE",
                        detail=f"camera {camera_id} back online at {ip}",
                    )

    def check_offline(self):
        """Mark cameras as offline if no heartbeat received within OFFLINE_TIMEOUT."""
        now = datetime.now(UTC)
        cameras = self._store.get_cameras()

        for camera in cameras:
            if camera.status not in ("online",):
                continue
            if not camera.last_seen:
                continue

            try:
                last = datetime.fromisoformat(camera.last_seen.replace("Z", "+00:00"))
                elapsed = (now - last).total_seconds()
            except (ValueError, TypeError):
                continue

            if elapsed > OFFLINE_TIMEOUT:
                camera.status = "offline"
                # Clear streaming flag — we cannot trust stale state (ADR-0016)
                camera.streaming = False
                self._store.save_camera(camera)
                if self._audit:
                    self._audit.log_event(
                        "CAMERA_OFFLINE",
                        detail=f"camera {camera.id} offline (last seen {int(elapsed)}s ago)",
                    )

    def get_camera_status(self, camera_id):
        """Get current status info for a camera."""
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return None
        return {
            "id": camera.id,
            "name": camera.name,
            "status": camera.status,
            "ip": camera.ip,
            "last_seen": camera.last_seen,
            "firmware_version": camera.firmware_version,
            "resolution": camera.resolution,
            "fps": camera.fps,
        }

    # -------------------------------------------------------------------------
    # mDNS browser (RFC 6762 / RFC 6763 DNS-SD via python-zeroconf)
    # -------------------------------------------------------------------------

    def start_mdns_browser(self):
        """Start background mDNS browser for _rtsp._tcp cameras.

        Uses python-zeroconf (same library as Home Assistant, Frigate).
        The ServiceBrowser runs in daemon threads and calls _on_mdns_service_change
        whenever a camera advertisement is added or updated. That callback
        calls report_camera() — the same path as heartbeat / self-registration.

        Falls back gracefully if zeroconf is not installed.
        """
        if self._zeroconf is not None:
            log.debug("mDNS browser already running")
            return

        try:
            from zeroconf import ServiceBrowser, Zeroconf
        except ImportError:
            log.warning(
                "python-zeroconf not installed — mDNS auto-discovery disabled. "
                "Install with: pip3 install 'zeroconf>=0.100'"
            )
            return

        try:
            self._zeroconf = Zeroconf()
            # ServiceBrowser is non-blocking — spawns its own daemon threads
            self._mdns_browser = ServiceBrowser(
                self._zeroconf,
                _MDNS_SERVICE_TYPE,
                handlers=[self._on_mdns_service_change],
            )
            log.info(
                "mDNS browser started — listening for %s cameras", _MDNS_SERVICE_TYPE
            )
        except Exception as exc:
            log.warning("Failed to start mDNS browser: %s", exc)
            if self._zeroconf:
                try:
                    self._zeroconf.close()
                except Exception:
                    pass
            self._zeroconf = None
            self._mdns_browser = None

    def stop_mdns_browser(self):
        """Stop mDNS browser and release socket resources."""
        if self._zeroconf is None:
            return
        try:
            self._zeroconf.close()
        except Exception:
            pass
        finally:
            self._zeroconf = None
            self._mdns_browser = None
            log.info("mDNS browser stopped")

    def trigger_scan(self):
        """Request an immediate mDNS PTR query (for manual Scan button).

        The background ServiceBrowser already runs continuously and discovers
        cameras as they appear. This method sends an extra PTR query so that
        newly powered-on cameras are detected faster when the user clicks Scan.

        Fails silently — the background browser is always the primary path.
        """
        if self._zeroconf is None:
            log.debug("mDNS browser not running — scan request ignored")
            return

        try:
            # zeroconf's public send API: build a PTR question and multicast it
            from zeroconf import DNSOutgoing, DNSQuestion

            # _TYPE_PTR = 12, _CLASS_IN = 1 per RFC 1035 / RFC 6762
            _TYPE_PTR = 12
            _CLASS_IN = 1

            out = DNSOutgoing(0)  # flags=0 → standard query
            out.add_question(DNSQuestion(_MDNS_SERVICE_TYPE, _TYPE_PTR, _CLASS_IN))
            self._zeroconf.send(out)
            log.debug("Sent manual mDNS PTR query for %s", _MDNS_SERVICE_TYPE)
        except Exception as exc:
            # Non-fatal: background browser already handles discovery
            log.debug("Manual mDNS PTR query failed (non-fatal): %s", exc)

    # -------------------------------------------------------------------------
    # Internal mDNS callbacks
    # -------------------------------------------------------------------------

    def _on_mdns_service_change(self, zeroconf, service_type, name, state_change):
        """Called by ServiceBrowser on any service state change."""
        try:
            from zeroconf import ServiceStateChange
        except ImportError:
            return

        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            self._handle_mdns_service(zeroconf, service_type, name)
        # Removed: camera going offline is handled by the staleness checker
        # (check_offline) via heartbeat timeout — mDNS removals are unreliable.

    def _handle_mdns_service(self, zeroconf, service_type, name):
        """Parse a discovered mDNS service record and call report_camera()."""
        try:
            info = zeroconf.get_service_info(service_type, name)
            if not info:
                log.debug("mDNS: no service info for %s", name)
                return

            # Parse TXT records (keys and values arrive as bytes)
            props = {}
            for k, v in (info.properties or {}).items():
                key = k.decode("utf-8", errors="replace") if isinstance(k, bytes) else k
                val = (
                    v.decode("utf-8", errors="replace")
                    if isinstance(v, bytes)
                    else (v or "")
                )
                props[key] = val

            camera_id = props.get("id", "")
            if not camera_id or not camera_id.startswith(_CAMERA_ID_PREFIX):
                # Not a HomeMonitor camera — ignore (other _rtsp._tcp services)
                log.debug("mDNS: ignoring non-HomeMonitor service %s", name)
                return

            # Resolve IP address — prefer parsed_addresses() (modern zeroconf)
            ip = ""
            if hasattr(info, "parsed_addresses"):
                addrs = info.parsed_addresses()
                # Prefer IPv4
                for addr in addrs:
                    if ":" not in addr:  # not IPv6
                        ip = addr
                        break
                if not ip and addrs:
                    ip = addrs[0]
            elif info.addresses:
                try:
                    ip = socket.inet_ntoa(info.addresses[0])
                except Exception:
                    pass

            if not ip:
                log.debug("mDNS: no usable address for %s", name)
                return

            firmware_version = props.get("version", "")
            paired_str = props.get("paired", "false")

            log.info(
                "mDNS: camera %s at %s (paired=%s firmware=%s)",
                camera_id,
                ip,
                paired_str,
                firmware_version or "?",
            )
            self.report_camera(camera_id, ip, firmware_version)

        except Exception as exc:
            log.warning("mDNS handler error for '%s': %s", name, exc)

# REQ: SWR-051; RISK: RISK-020, RISK-005; SEC: SC-020; TEST: TC-046
"""
Camera hardware fault codes + helpers.

A camera can be perfectly reachable over the network (heartbeats
arriving on time, `status: online` on the server) and still have
something wrong with it: missing sensor, storage almost full,
thermal throttling, etc. These are different surfaces from
offline/online and shouldn't collapse into one flag.

Each fault carries:
- ``code``: machine-readable identifier (stable — external dashboards
  and the audit log key on this).
- ``severity``: how loud the UI should be about it.
- ``message``: human-readable single sentence. The dashboard puts
  it on the card banner verbatim.

The camera-side heartbeat emits a list of active faults. The server
stores the list on the Camera record and the dashboard renders
one banner per entry next to the ONLINE badge — so operators see
"camera is online, but sensor is missing" instead of wondering why
a green tile isn't streaming.

Designed as a module-level constant set rather than an Enum so the
wire format (JSON) stays a plain string — no enum-name serialization
surprises across Python + JS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------
# Severity levels — ordered so ``max(severity, other)`` is meaningful
# ---------------------------------------------------------------

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

VALID_SEVERITIES = (
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SEVERITY_ERROR,
    SEVERITY_CRITICAL,
)

# Numeric rank so the UI can pick "the loudest active fault" when
# rendering a single colour on a compact card.
_SEVERITY_RANK = {
    SEVERITY_INFO: 0,
    SEVERITY_WARNING: 1,
    SEVERITY_ERROR: 2,
    SEVERITY_CRITICAL: 3,
}


def severity_rank(severity: str) -> int:
    """Return the numeric rank of a severity string; unknown → 0."""
    return _SEVERITY_RANK.get(severity, 0)


# ---------------------------------------------------------------
# Fault codes
# ---------------------------------------------------------------
#
# Naming: ``<subsystem>_<condition>``, lowercase, underscore.
# Once shipped, DO NOT rename — server-side audit queries,
# dashboards, and external integrations key on these strings.
# Deprecate by emitting a replacement alongside and phasing out
# over one release.
#
# Add new codes here and register them in ``FAULT_DEFAULTS`` with
# a default severity + short human message.

# Camera subsystem
FAULT_CAMERA_SENSOR_MISSING = "camera_sensor_missing"
FAULT_CAMERA_H264_UNSUPPORTED = "camera_h264_unsupported"

# (Reserved for upcoming work — not emitted yet, kept here so
# dashboard string tables can be written against a stable set.)
FAULT_STORAGE_LOW = "storage_low"
FAULT_STORAGE_UNWRITABLE = "storage_unwritable"
FAULT_THERMAL_THROTTLING = "thermal_throttling"
FAULT_NETWORK_SERVER_UNREACHABLE = "network_server_unreachable"
FAULT_OTA_FAILED = "ota_failed"


# Default severity + short message per code. Call-sites can override
# ``message`` to add device-specific detail.
FAULT_DEFAULTS: dict[str, dict[str, str]] = {
    FAULT_CAMERA_SENSOR_MISSING: {
        "severity": SEVERITY_ERROR,
        "message": "Camera sensor missing",
        "hint": (
            "Check the ribbon cable is seated firmly at both ends and "
            "reboot the camera. The image supports OV5647, IMX219, "
            "IMX477 and IMX708 sensors via firmware auto-detect; no "
            "manual config.txt edit is needed for a swap."
        ),
    },
    FAULT_CAMERA_H264_UNSUPPORTED: {
        "severity": SEVERITY_WARNING,
        "message": "H.264 encode unavailable",
        "hint": (
            "Driver doesn't advertise hardware H.264 and libcamera-vid "
            "is also missing. Streaming will fail or fall back to "
            "software encoding with high CPU."
        ),
    },
    FAULT_STORAGE_LOW: {
        "severity": SEVERITY_WARNING,
        "message": "Storage low",
        "hint": "Recording storage is below the configured low-water mark.",
    },
    FAULT_STORAGE_UNWRITABLE: {
        "severity": SEVERITY_CRITICAL,
        "message": "Storage not writable",
        "hint": "Recording storage is not writable — new clips cannot be saved.",
    },
    FAULT_THERMAL_THROTTLING: {
        "severity": SEVERITY_WARNING,
        "message": "Thermal throttling",
        "hint": "CPU is thermal-throttling — stream quality may degrade.",
    },
    FAULT_NETWORK_SERVER_UNREACHABLE: {
        "severity": SEVERITY_ERROR,
        "message": "Server unreachable",
        "hint": "Cannot reach the paired server (heartbeat failing).",
    },
    FAULT_OTA_FAILED: {
        "severity": SEVERITY_ERROR,
        "message": "Last OTA failed",
        "hint": "The last firmware update did not install. Check Settings → Updates.",
    },
}


# ---------------------------------------------------------------
# Fault record — the wire shape
# ---------------------------------------------------------------


@dataclass
class Fault:
    """A single active hardware / system fault.

    UX split (on the dashboard):
      * ``message`` — short label. Fits on a card badge (~24 chars).
      * ``hint``    — longer actionable advice. Rendered as the
                      badge's ``title=`` attribute so hovering shows
                      it, without crowding the card.
    The short/long split keeps the dashboard scannable while still
    giving operators the "what do I do" text without a drill-in.
    """

    code: str
    severity: str = SEVERITY_WARNING
    message: str = ""
    hint: str = ""
    # Optional supplementary context for debugging. Free-form; the
    # dashboard only surfaces well-known keys and ignores the rest.
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise to the heartbeat wire format."""
        return asdict(self)


def make_fault(
    code: str,
    *,
    severity: str | None = None,
    message: str | None = None,
    hint: str | None = None,
    context: dict | None = None,
) -> Fault:
    """Build a Fault, filling severity/message/hint from FAULT_DEFAULTS if omitted.

    Call-sites typically pass just ``code`` (plus optional context)
    and rely on the central default copy. Override individual
    fields for device-specific detail.
    """
    defaults = FAULT_DEFAULTS.get(code, {})
    return Fault(
        code=code,
        severity=severity or defaults.get("severity", SEVERITY_WARNING),
        message=message or defaults.get("message", ""),
        hint=hint or defaults.get("hint", ""),
        context=dict(context or {}),
    )

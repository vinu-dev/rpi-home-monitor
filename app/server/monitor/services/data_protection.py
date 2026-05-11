# REQ: SWR-101-A; RISK: RISK-101-1; SEC: SC-005, SC-101; TEST: TC-101-AC-12
"""Runtime data-at-rest protection posture for the server data volume."""

from __future__ import annotations

from pathlib import Path


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "required"}


def _mountinfo_unescape(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


class DataProtectionService:
    """Detect and enforce the at-rest protection posture for ``/data``.

    The LUKS migration is still an operator-controlled track, so this service
    does not claim encryption is enabled just because docs say it should be.
    It reads the live mount table and dm-crypt metadata every time status is
    requested.
    """

    def __init__(
        self,
        *,
        data_dir: str = "/data",
        require_encrypted: bool | str = False,
        require_marker_path: str | None = None,
        proc_mountinfo_path: str = "/proc/self/mountinfo",
        sys_dev_block_root: str = "/sys/dev/block",
    ) -> None:
        self._data_dir = str(data_dir).rstrip("/") or "/"
        self._require_encrypted = _truthy(require_encrypted)
        self._require_marker_path = require_marker_path
        self._proc_mountinfo_path = Path(proc_mountinfo_path)
        self._sys_dev_block_root = Path(sys_dev_block_root)

    def status(self) -> dict:
        """Return the live data-at-rest posture for API/UI/diagnostics."""

        required = self._enforcement_required()
        mount, error = self._find_mount()
        if mount is None:
            state = "unknown"
            protected = False
            message = f"Could not determine /data encryption state: {error}"
            details = {"error": error}
        else:
            dm_uuid = self._read_dm_uuid(mount["major_minor"])
            details = {**mount, "dm_uuid": dm_uuid}
            if dm_uuid.startswith("CRYPT-LUKS"):
                state = "encrypted"
                protected = True
                message = "/data is mounted through LUKS/dm-crypt."
            elif dm_uuid:
                state = "unencrypted"
                protected = False
                message = (
                    "/data is mounted through device-mapper, but not as a "
                    "LUKS/dm-crypt volume."
                )
            else:
                state = "unencrypted"
                protected = False
                message = (
                    "/data is mounted without LUKS/dm-crypt; persisted secrets "
                    "remain plaintext-on-data."
                )

        blocked = required and state != "encrypted"
        warning = ""
        if blocked:
            warning = (
                "Encrypted /data is required by the active security profile. "
                "Secret-bearing enrollment is blocked until /data is encrypted."
            )
        elif state == "unencrypted":
            warning = (
                "Secrets on /data are plaintext-at-rest. Enable the LUKS "
                "migration before treating this device as theft-resistant."
            )
        elif state == "unknown":
            warning = (
                "The application could not prove whether /data is encrypted. "
                "Review diagnostics before enrolling new long-lived secrets."
            )

        return {
            "state": state,
            "protected": protected,
            "enforcement_required": required,
            "secret_enrollment_blocked": blocked,
            "requires_attention": bool(warning),
            "message": message,
            "warning": warning,
            "data_dir": self._data_dir,
            "details": details,
        }

    def check_secret_write_allowed(self, feature: str) -> tuple[bool, dict]:
        """Return whether a secret-bearing write may proceed."""

        posture = self.status()
        if not posture["secret_enrollment_blocked"]:
            return True, {}
        return False, {
            "error": "data_encryption_required",
            "feature": feature,
            "message": posture["warning"],
            "data_protection": posture,
        }

    def _enforcement_required(self) -> bool:
        if self._require_encrypted:
            return True
        if not self._require_marker_path:
            return False
        return Path(self._require_marker_path).is_file()

    def _find_mount(self) -> tuple[dict | None, str]:
        try:
            lines = self._proc_mountinfo_path.read_text().splitlines()
        except OSError as exc:
            return None, str(exc)

        target = self._data_dir
        matches: list[dict] = []
        for line in lines:
            parsed = self._parse_mountinfo_line(line)
            if parsed is None:
                continue
            mount_point = parsed["mount_point"]
            if target == mount_point or target.startswith(
                mount_point.rstrip("/") + "/"
            ):
                matches.append(parsed)

        if not matches:
            return None, f"no mount entry covers {target}"
        matches.sort(key=lambda item: len(item["mount_point"]), reverse=True)
        return matches[0], ""

    @staticmethod
    def _parse_mountinfo_line(line: str) -> dict | None:
        try:
            left, right = line.split(" - ", 1)
            left_parts = left.split()
            right_parts = right.split()
            if len(left_parts) < 5 or len(right_parts) < 3:
                return None
            return {
                "major_minor": left_parts[2],
                "root": _mountinfo_unescape(left_parts[3]),
                "mount_point": _mountinfo_unescape(left_parts[4]),
                "mount_options": left_parts[5] if len(left_parts) > 5 else "",
                "fstype": right_parts[0],
                "source": _mountinfo_unescape(right_parts[1]),
                "super_options": right_parts[2],
            }
        except ValueError:
            return None

    def _read_dm_uuid(self, major_minor: str) -> str:
        try:
            return (
                (self._sys_dev_block_root / major_minor / "dm" / "uuid")
                .read_text()
                .strip()
            )
        except OSError:
            return ""

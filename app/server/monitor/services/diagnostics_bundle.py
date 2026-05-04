# REQ: SWR-068, SWR-069, SWR-070; RISK: RISK-020, RISK-026; SEC: SC-020, SC-025; TEST: TC-055
"""Diagnostics export bundle assembly service."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import socket
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from monitor.release_version import release_version
from monitor.utils.redact import REDACT_PATHS, redact_secrets

log = logging.getLogger("monitor.services.diagnostics_bundle")

COMMAND_TIMEOUTS = {
    "df": 5,
    "ip": 5,
    "journalctl": 30,
    "systemctl": 10,
    "vcgencmd": 5,
}
SECTION_ORDER = ("logs", "config", "hardware", "network", "systemd", "identity")
TRUNCATION_PRIORITY = ("logs", "systemd", "network", "hardware", "identity", "config")


@dataclass
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int
    error: str = ""
    truncated: bool = False


@dataclass
class CollectedFile:
    path: str
    content: bytes
    error: str = ""
    truncated: bool = False

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass
class SectionSummary:
    name: str
    files: list[CollectedFile] = field(default_factory=list)
    error: str = ""
    truncated: bool = False

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def byte_size(self) -> int:
        return sum(item.size for item in self.files)


@dataclass
class BundleResult:
    run_id: str
    staging_path: str
    archive_path: str
    archive_bytes: int
    manifest: dict
    sections: list[dict]
    download_name: str


class DiagnosticsBundleDownloadStream:
    """File-like archive stream that cleans up staging once closed."""

    def __init__(self, archive_path: str | Path, cleanup_callback) -> None:
        self._archive_path = Path(archive_path)
        self._cleanup_callback = cleanup_callback
        self._handle = self._archive_path.open("rb")
        self._closed = False
        self.name = self._handle.name
        self.mode = self._handle.mode

    def read(self, *args, **kwargs):
        return self._handle.read(*args, **kwargs)

    def seek(self, *args, **kwargs):
        return self._handle.seek(*args, **kwargs)

    def tell(self) -> int:
        return self._handle.tell()

    def seekable(self) -> bool:
        return self._handle.seekable()

    def readable(self) -> bool:
        return self._handle.readable()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._handle.close()
        finally:
            self._cleanup_callback()


class DiagnosticsBundleError(RuntimeError):
    """Raised when a diagnostics export cannot be completed."""

    def __init__(
        self,
        *,
        error: str,
        status_code: int,
        detail: str = "",
        retry_after_seconds: int = 0,
    ) -> None:
        super().__init__(detail or error)
        self.error = error
        self.status_code = status_code
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds

    @property
    def payload(self) -> dict:
        payload = {"error": self.error}
        if self.detail:
            payload["detail"] = self.detail
        if self.retry_after_seconds:
            payload["retry_after_seconds"] = self.retry_after_seconds
        return payload


class DiagnosticsBundleService:
    """Build a bounded diagnostics archive for admin download."""

    def __init__(
        self,
        *,
        data_dir: str,
        config_dir: str,
        store,
        audit,
        max_bytes: int,
        timeout_seconds: int,
        section_caps: dict[str, int],
        units: list[str] | tuple[str, ...],
        rate_limit_per_session: int = 6,
        rate_limit_window_seconds: int = 60 * 60,
        cleanup_grace_seconds: int = 60,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._config_dir = Path(config_dir)
        self._logs_dir = self._data_dir / "logs"
        self._store = store
        self._audit = audit
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._section_caps = dict(section_caps)
        self._units = list(units)
        self._staging_root = self._config_dir / "diagnostics-staging"
        self._rate_limit_per_session = rate_limit_per_session
        self._rate_limit_window_seconds = rate_limit_window_seconds
        self._cleanup_grace_seconds = cleanup_grace_seconds
        self._export_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_run_id = ""
        self._cleanup_timers: dict[str, threading.Timer] = {}
        self._session_attempts: dict[str, list[float]] = {}

    def check_rate_limit(self, session_id: str) -> tuple[bool, int]:
        """Return whether the caller may start a new diagnostics export."""

        if not session_id:
            return True, 0

        now = time.time()
        with self._state_lock:
            attempts = [
                stamp
                for stamp in self._session_attempts.get(session_id, [])
                if now - stamp < self._rate_limit_window_seconds
            ]
            self._session_attempts[session_id] = attempts
            if len(attempts) >= self._rate_limit_per_session:
                retry_after = max(
                    1,
                    int(self._rate_limit_window_seconds - (now - attempts[0])),
                )
                return False, retry_after
            attempts.append(now)
            self._session_attempts[session_id] = attempts
        return True, 0

    def collect_sections(
        self,
        *,
        requested_by: str,
        requested_ip: str,
    ) -> BundleResult:
        """Collect all bundle sections and write the archive to staging."""

        run_id = uuid.uuid4().hex[:12]
        if not self._export_lock.acquire(blocking=False):
            raise DiagnosticsBundleError(
                error="diagnostics_export_in_progress",
                status_code=429,
                retry_after_seconds=15,
            )

        with self._state_lock:
            self._active_run_id = run_id

        started = time.monotonic()
        deadline = started + self._timeout_seconds
        run_dir = self._staging_root / run_id
        settings = self._store.get_settings()
        host = _sanitize_hostname(
            getattr(settings, "hostname", "") or socket.gethostname()
        )
        generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        bundle_name = f"hm-diagnostics-{host}-{generated_at}"
        bundle_root = run_dir / bundle_name
        archive_path = run_dir / f"{bundle_name}.tar.gz"
        redactions: list[dict] = []
        aborted = False
        tool_versions = self._tool_versions()

        try:
            self._purge_stale_runs()
            run_dir.mkdir(parents=True, exist_ok=True)
            bundle_root.mkdir(parents=True, exist_ok=True)
            self._schedule_cleanup(run_id)
        except OSError as exc:
            self.cleanup(run_id)
            self._audit_failure(
                user=requested_by,
                ip=requested_ip,
                reason="diagnostics_staging_failed",
                detail=str(exc),
            )
            raise DiagnosticsBundleError(
                error="diagnostics_staging_failed",
                status_code=503,
                detail=str(exc),
            ) from exc

        try:
            sections: dict[str, SectionSummary] = {
                name: SectionSummary(name=name) for name in SECTION_ORDER
            }

            config_section, config_redactions = self._collect_config()
            sections["config"] = config_section
            redactions.extend(config_redactions)

            for name, collector in (
                ("identity", self._collect_identity),
                ("hardware", self._collect_hardware),
                ("network", self._collect_network),
                ("systemd", self._collect_systemd),
                ("logs", self._collect_logs),
            ):
                if time.monotonic() > deadline:
                    aborted = True
                    sections[name].error = "collection timed out"
                    continue
                sections[name] = collector(deadline=deadline)

            self._apply_section_caps(sections)
            self._apply_total_cap(sections)
            manifest = self._build_manifest(
                host=host,
                firmware_version=release_version(),
                requested_by=_requested_by_label(requested_by, requested_ip),
                sections=sections,
                redactions=redactions,
                tool_versions=tool_versions,
                aborted=aborted,
            )
            self._write_bundle(
                bundle_root=bundle_root, sections=sections, manifest=manifest
            )
            self._create_archive(archive_path=archive_path, bundle_root=bundle_root)
            archive_bytes = archive_path.stat().st_size
            duration_ms = int((time.monotonic() - started) * 1000)
            self._audit_success(
                user=requested_by,
                ip=requested_ip,
                archive_bytes=archive_bytes,
                sections=sections,
                duration_ms=duration_ms,
                aborted=aborted,
            )
            return BundleResult(
                run_id=run_id,
                staging_path=str(run_dir),
                archive_path=str(archive_path),
                archive_bytes=archive_bytes,
                manifest=manifest,
                sections=manifest["sections"],
                download_name=archive_path.name,
            )
        except DiagnosticsBundleError:
            self.cleanup(run_id)
            raise
        except Exception as exc:
            log.exception("Diagnostics export failed")
            self.cleanup(run_id)
            self._audit_failure(
                user=requested_by,
                ip=requested_ip,
                reason="diagnostics_export_failed",
                detail=str(exc),
            )
            raise DiagnosticsBundleError(
                error="diagnostics_export_failed",
                status_code=500,
            ) from exc

    def cleanup(self, run_id: str) -> None:
        """Remove staged files for one diagnostics export and release the lock."""

        timer = None
        with self._state_lock:
            timer = self._cleanup_timers.pop(run_id, None)
        if timer is not None:
            timer.cancel()

        run_dir = self._staging_root / run_id
        try:
            if run_dir.exists():
                shutil.rmtree(run_dir, ignore_errors=True)
        finally:
            self._release_run(run_id)

    def _release_run(self, run_id: str) -> None:
        with self._state_lock:
            if self._active_run_id != run_id:
                return
            self._active_run_id = ""
        if self._export_lock.locked():
            self._export_lock.release()

    def open_archive_stream(
        self, result: BundleResult
    ) -> DiagnosticsBundleDownloadStream:
        """Open the staged archive and clean it up when the stream closes."""

        return DiagnosticsBundleDownloadStream(
            result.archive_path,
            lambda: self.cleanup(result.run_id),
        )

    def _schedule_cleanup(self, run_id: str) -> None:
        timer = threading.Timer(
            self._cleanup_grace_seconds, lambda: self.cleanup(run_id)
        )
        timer.daemon = True
        with self._state_lock:
            self._cleanup_timers[run_id] = timer
        timer.start()

    def _purge_stale_runs(self) -> None:
        if not self._staging_root.exists():
            return
        cutoff = time.time() - (self._cleanup_grace_seconds * 4)
        for entry in self._staging_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    def _collect_config(self) -> tuple[SectionSummary, list[dict]]:
        section = SectionSummary(name="config")
        redactions: list[dict] = []

        users_payload = {
            "users": [asdict(user) for user in self._store.get_users()],
        }
        cameras_payload = {
            "cameras": [asdict(camera) for camera in self._store.get_cameras()],
        }
        settings_payload = asdict(self._store.get_settings())

        for filename, payload, paths in (
            ("config/users.json", users_payload, REDACT_PATHS.users),
            ("config/cameras.json", cameras_payload, REDACT_PATHS.cameras),
            ("config/settings.json", settings_payload, REDACT_PATHS.settings),
        ):
            scrubbed = redact_secrets(payload, paths)
            section.files.append(
                CollectedFile(
                    path=filename,
                    content=_json_bytes(scrubbed),
                )
            )
            redactions.append({"file": filename, "fields": list(paths)})

        return section, redactions

    def _collect_identity(self, *, deadline: float) -> SectionSummary:
        section = SectionSummary(name="identity")
        section.files.extend(
            [
                CollectedFile(
                    path="identity/os-release.txt",
                    content=self._read_text_file("/etc/os-release"),
                ),
                CollectedFile(
                    path="identity/release_version.txt",
                    content=(release_version() + "\n").encode("utf-8"),
                ),
                CollectedFile(
                    path="identity/hostname.txt",
                    content=((socket.gethostname() or "host") + "\n").encode("utf-8"),
                ),
            ]
        )
        return section

    def _collect_hardware(self, *, deadline: float) -> SectionSummary:
        section = SectionSummary(name="hardware")
        for argv, filename in (
            (["vcgencmd", "measure_temp"], "hardware/vcgencmd-measure_temp.txt"),
            (
                ["vcgencmd", "measure_clock", "arm"],
                "hardware/vcgencmd-measure_clock.txt",
            ),
            (["vcgencmd", "get_throttled"], "hardware/vcgencmd-get_throttled.txt"),
            (["vcgencmd", "measure_volts"], "hardware/vcgencmd-measure_volts.txt"),
        ):
            if time.monotonic() > deadline:
                section.error = "collection timed out"
                section.truncated = True
                break
            section.files.append(self._command_file(filename, argv, "vcgencmd"))

        for path, filename in (
            ("/proc/meminfo", "hardware/meminfo.txt"),
            ("/proc/uptime", "hardware/uptime.txt"),
            ("/proc/cpuinfo", "hardware/cpuinfo.txt"),
            ("/proc/loadavg", "hardware/loadavg.txt"),
            ("/sys/class/thermal/thermal_zone0/temp", "hardware/thermal.txt"),
        ):
            section.files.append(
                CollectedFile(path=filename, content=self._read_text_file(path))
            )

        section.files.append(self._command_file("hardware/df.txt", ["df", "-h"], "df"))
        return section

    def _collect_network(self, *, deadline: float) -> SectionSummary:
        section = SectionSummary(name="network")
        section.files.append(
            self._command_file("network/ip-addr.json", ["ip", "-j", "addr"], "ip")
        )
        section.files.append(
            self._command_file("network/ip-route.json", ["ip", "-j", "route"], "ip")
        )
        section.files.append(
            CollectedFile(
                path="network/interfaces.txt",
                content=self._network_interface_snapshot(),
            )
        )
        section.files.append(
            CollectedFile(
                path="network/resolv.conf",
                content=self._read_text_file("/etc/resolv.conf"),
            )
        )
        return section

    def _collect_systemd(self, *, deadline: float) -> SectionSummary:
        section = SectionSummary(name="systemd")
        status_chunks: list[str] = []
        for unit in self._units:
            if time.monotonic() > deadline:
                section.error = "collection timed out"
                section.truncated = True
                break
            unit_slug = unit.replace(".service", "").replace("@", "-")
            journal = self._command_file(
                f"systemd/{unit_slug}.journal.txt",
                ["journalctl", "-u", unit, "--since=-7d", "--no-pager"],
                "journalctl",
            )
            section.files.append(journal)

            status = self._run_command(
                ["systemctl", "status", unit, "--no-pager", "--lines=200"],
                timeout_seconds=COMMAND_TIMEOUTS["systemctl"],
                cap_bytes=self._section_caps.get("systemd", self._max_bytes),
            )
            status_chunks.append(f"== {unit} ==\n")
            status_chunks.append(_command_text(status))
            if not status_chunks[-1].endswith("\n"):
                status_chunks.append("\n")

        section.files.append(
            CollectedFile(
                path="systemd/systemctl-status.txt",
                content="".join(status_chunks).encode("utf-8"),
            )
        )
        return section

    def _collect_logs(self, *, deadline: float) -> SectionSummary:
        section = SectionSummary(name="logs")
        root = self._logs_dir
        if not root.exists():
            section.error = "logs directory missing"
            return section

        safe_root = root.resolve()
        files = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: str(path.relative_to(root)),
        )
        for path in files:
            if time.monotonic() > deadline:
                section.error = "collection timed out"
                section.truncated = True
                break
            try:
                resolved = path.resolve()
            except OSError:
                section.files.append(
                    CollectedFile(
                        path=f"logs/{path.relative_to(root).as_posix()}",
                        content=b"",
                        error="path resolve failed",
                    )
                )
                continue
            if not _is_relative_to(resolved, safe_root):
                section.files.append(
                    CollectedFile(
                        path=f"logs/{path.relative_to(root).as_posix()}",
                        content=b"",
                        error="path escape",
                    )
                )
                continue
            section.files.append(
                CollectedFile(
                    path=f"logs/{path.relative_to(root).as_posix()}",
                    content=self._read_bytes_file(path),
                )
            )
        return section

    def _command_file(
        self, path: str, argv: list[str], tool_name: str
    ) -> CollectedFile:
        result = self._run_command(
            argv,
            timeout_seconds=COMMAND_TIMEOUTS.get(tool_name, 5),
            cap_bytes=self._section_caps.get(path.split("/", 1)[0], self._max_bytes),
        )
        return CollectedFile(
            path=path,
            content=_command_text(result).encode("utf-8"),
            error=result.error,
            truncated=result.truncated,
        )

    def _run_command(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        cap_bytes: int,
    ) -> CommandResult:
        tool = argv[0]
        if shutil.which(tool) is None:
            marker = b"<command not available on this platform>\n"
            return CommandResult(marker, b"", 127, error="command not available")

        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            marker = b"<command timed out>\n"
            return CommandResult(marker, b"", 124, error="command timed out")
        except OSError as exc:
            marker = f"<command failed: {exc}>\n".encode("utf-8", errors="replace")
            return CommandResult(marker, b"", 126, error=str(exc))

        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        if completed.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or "non-zero exit"
            marker = f"<command failed: {detail}>\n".encode("utf-8", errors="replace")
            return CommandResult(marker, stderr, completed.returncode, error=detail)

        stdout, truncated = _truncate_bytes(stdout, cap_bytes)
        return CommandResult(stdout, stderr, completed.returncode, truncated=truncated)

    def _apply_section_caps(self, sections: dict[str, SectionSummary]) -> None:
        for name, summary in sections.items():
            summary.files, truncated = _cap_files(
                summary.files,
                self._section_caps.get(name, self._max_bytes),
            )
            if truncated:
                summary.truncated = True
                if not summary.error:
                    summary.error = "section cap reached"

    def _apply_total_cap(self, sections: dict[str, SectionSummary]) -> None:
        total_bytes = sum(summary.byte_size for summary in sections.values())
        if total_bytes <= self._max_bytes:
            return

        overflow = total_bytes - self._max_bytes
        for name in TRUNCATION_PRIORITY:
            if overflow <= 0:
                break
            summary = sections[name]
            if summary.byte_size <= 0:
                continue
            new_limit = max(0, summary.byte_size - overflow)
            summary.files, truncated = _cap_files(summary.files, new_limit)
            if truncated:
                summary.truncated = True
                if not summary.error:
                    summary.error = "bundle cap reached"
            overflow = sum(s.byte_size for s in sections.values()) - self._max_bytes

    def _write_bundle(
        self,
        *,
        bundle_root: Path,
        sections: dict[str, SectionSummary],
        manifest: dict,
    ) -> None:
        manifest_path = bundle_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for name in SECTION_ORDER:
            summary = sections[name]
            for item in summary.files:
                target = bundle_root / item.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item.content)

    def _create_archive(self, *, archive_path: Path, bundle_root: Path) -> None:
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(bundle_root, arcname=bundle_root.name)

    def _build_manifest(
        self,
        *,
        host: str,
        firmware_version: str,
        requested_by: str,
        sections: dict[str, SectionSummary],
        redactions: list[dict],
        tool_versions: dict[str, str],
        aborted: bool,
    ) -> dict:
        section_rows: list[dict] = []
        for name in SECTION_ORDER:
            summary = sections[name]
            row = {
                "name": name,
                "file_count": summary.file_count,
                "byte_size": summary.byte_size,
                "truncated": summary.truncated,
                "error": summary.error,
            }
            files = []
            for item in summary.files:
                files.append(
                    {
                        "name": item.path.split("/", 1)[1]
                        if "/" in item.path
                        else item.path,
                        "size": item.size,
                        "sha256": hashlib.sha256(item.content).hexdigest(),
                        "error": item.error,
                        "truncated": item.truncated,
                    }
                )
            if files:
                row["files"] = files
            section_rows.append(row)

        return {
            "bundle_version": 1,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "host": host,
            "firmware_version": firmware_version,
            "requested_by": requested_by,
            "sections": section_rows,
            "redactions": redactions,
            "tool_versions": tool_versions,
            "aborted": aborted,
        }

    def _tool_versions(self) -> dict[str, str]:
        versions = {}
        commands = {
            "vcgencmd": ["vcgencmd", "version"],
            "journalctl": ["journalctl", "--version"],
            "ip": ["ip", "-Version"],
        }
        for name, argv in commands.items():
            result = self._run_command(
                argv,
                timeout_seconds=COMMAND_TIMEOUTS.get(name, 5),
                cap_bytes=1024,
            )
            versions[name] = _first_line(_command_text(result))
        return versions

    def _audit_success(
        self,
        *,
        user: str,
        ip: str,
        archive_bytes: int,
        sections: dict[str, SectionSummary],
        duration_ms: int,
        aborted: bool,
    ) -> None:
        if not self._audit:
            return
        detail = json.dumps(
            {
                "bytes": archive_bytes,
                "sections": len(
                    [item for item in sections.values() if item.file_count > 0]
                ),
                "duration_ms": duration_ms,
                "truncated_sections": [
                    item.name for item in sections.values() if item.truncated
                ],
                "aborted": aborted,
            },
            separators=(",", ":"),
        )
        self._audit.log_event(
            "DIAGNOSTICS_EXPORTED",
            user=user,
            ip=ip,
            detail=detail,
        )

    def _audit_failure(
        self,
        *,
        user: str,
        ip: str,
        reason: str,
        detail: str,
    ) -> None:
        if not self._audit:
            return
        payload = json.dumps(
            {
                "reason": reason,
                "detail": detail[:256],
            },
            separators=(",", ":"),
        )
        self._audit.log_event(
            "DIAGNOSTICS_EXPORT_FAILED",
            user=user,
            ip=ip,
            detail=payload,
        )

    def _read_text_file(self, path: str) -> bytes:
        return (
            self._read_bytes_file(Path(path))
            .decode("utf-8", errors="replace")
            .encode("utf-8")
        )

    def _read_bytes_file(self, path: str | Path) -> bytes:
        try:
            return Path(path).read_bytes()
        except OSError:
            return b"<file not available on this platform>\n"

    def _network_interface_snapshot(self) -> bytes:
        net_root = Path("/sys/class/net")
        if not net_root.exists():
            return b"<network interface state not available on this platform>\n"

        lines: list[str] = []
        for iface in sorted(net_root.iterdir()):
            if iface.name == "lo":
                continue
            operstate = _safe_read_text(iface / "operstate", default="unknown")
            address = _safe_read_text(iface / "address", default="")
            lines.append(f"{iface.name}: state={operstate} mac={address}\n")
        if not lines:
            return b"<no interfaces discovered>\n"
        return "".join(lines).encode("utf-8")


def _json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _command_text(result: CommandResult) -> str:
    return result.stdout.decode("utf-8", errors="replace")


def _requested_by_label(user: str, ip: str) -> str:
    actor = user or "unknown"
    if ip:
        return f"{actor} @ {ip}"
    return actor


def _sanitize_hostname(hostname: str) -> str:
    clean = []
    previous_dash = False
    for char in hostname[:128]:
        if char.isalnum() or char in "._-":
            clean.append(char)
            previous_dash = False
            continue
        if not previous_dash:
            clean.append("-")
            previous_dash = True
    value = "".join(clean).strip("-.")[:64]
    return value or "host"


def _safe_read_text(path: Path, *, default: str) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default


def _cap_files(
    files: list[CollectedFile], limit: int
) -> tuple[list[CollectedFile], bool]:
    if limit <= 0:
        return [], bool(files)

    kept: list[CollectedFile] = []
    remaining = limit
    truncated = False
    for item in files:
        if remaining <= 0:
            truncated = True
            break
        if item.size <= remaining:
            kept.append(item)
            remaining -= item.size
            continue
        content, _ = _truncate_bytes(item.content, remaining)
        kept.append(
            CollectedFile(
                path=item.path,
                content=content,
                error=item.error,
                truncated=True,
            )
        )
        truncated = True
        remaining = 0
    return kept, truncated


def _truncate_bytes(content: bytes, limit: int) -> tuple[bytes, bool]:
    if len(content) <= limit:
        return content, False
    trailer = b"\n<truncated>\n"
    if limit <= len(trailer):
        return content[:limit], True
    return content[: limit - len(trailer)] + trailer, True


def _first_line(text: str) -> str:
    line = (text or "").splitlines()
    if not line:
        return "not available"
    return line[0][:200]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

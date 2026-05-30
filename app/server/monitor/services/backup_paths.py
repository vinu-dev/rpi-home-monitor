# REQ: SWR-018, SWR-034; RISK: RISK-006, RISK-019; SEC: SC-006, SC-017; TEST: TC-015, TC-032
"""
Shared path catalogue for server-side mutable configuration.

Config backup/import and factory reset share this catalogue so their path
inventory is explicit. Factory reset intentionally keeps the operator-chosen
setup hotspot password: the authenticated admin reset path must not fall back
to the public first-boot default.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BackupPaths:
    """Resolved filesystem paths for mutable server state."""

    data_dir: Path
    config_dir: Path
    certs_dir: Path

    @property
    def users_file(self) -> Path:
        return self.config_dir / "users.json"

    @property
    def cameras_file(self) -> Path:
        return self.config_dir / "cameras.json"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def hostname_file(self) -> Path:
        return self.config_dir / "hostname"

    @property
    def session_secret_file(self) -> Path:
        return self.config_dir / ".secret_key"

    @property
    def setup_hotspot_password_file(self) -> Path:
        return self.config_dir / "setup-hotspot.psk"

    @property
    def motion_events_file(self) -> Path:
        return self.config_dir / "motion_events.json"

    @property
    def alert_read_state_file(self) -> Path:
        return self.config_dir / "alert_read_state.json"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def live_dir(self) -> Path:
        return self.data_dir / "live"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def tailscale_dir(self) -> Path:
        return self.data_dir / "tailscale"

    @property
    def ota_dir(self) -> Path:
        return self.data_dir / "ota"

    @property
    def wifi_connections_dir(self) -> Path:
        return self.data_dir / "network" / "system-connections"

    @property
    def wifi_wiped_marker(self) -> Path:
        return self.data_dir / "network" / ".wifi-wiped"

    @property
    def backup_snapshot_root(self) -> Path:
        return self.data_dir / "backup-snapshots"

    @property
    def resettable_config_files(self) -> tuple[Path, ...]:
        return (
            self.cameras_file,
            self.users_file,
            self.settings_file,
            self.session_secret_file,
            self.hostname_file,
            self.motion_events_file,
            self.alert_read_state_file,
        )

    @property
    def resettable_dirs(self) -> tuple[Path, ...]:
        return (
            self.certs_dir,
            self.live_dir,
            self.recordings_dir,
            self.logs_dir,
            self.tailscale_dir,
            self.ota_dir,
        )


def build_backup_paths(
    data_dir: str = "/data",
    config_dir: str | None = None,
    certs_dir: str | None = None,
) -> BackupPaths:
    """Resolve the path catalogue from the app's configured roots."""
    data_root = Path(data_dir)
    config_root = Path(config_dir) if config_dir else data_root / "config"
    certs_root = Path(certs_dir) if certs_dir else data_root / "certs"
    return BackupPaths(
        data_dir=data_root,
        config_dir=config_root,
        certs_dir=certs_root,
    )

"""
JSON file persistence layer.

Provides thread-safe read/write for all data files on the /data partition:
  /data/config/cameras.json  — camera registry
  /data/config/users.json    — user accounts
  /data/config/settings.json — system settings

All writes use atomic replace (write to temp, then rename) to prevent
corruption if the process is killed mid-write.
"""

import json
import threading
from dataclasses import asdict
from pathlib import Path

from monitor.models import Camera, Settings, User


class Store:
    """Thread-safe JSON file store for all application data."""

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)
        self._lock = threading.Lock()
        self._ensure_dir()

    def _ensure_dir(self):
        """Create config directory if it doesn't exist."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _read_json(self, filename: str) -> list | dict:
        """Read a JSON file, returning empty list/dict if missing."""
        filepath = self.config_dir / filename
        if not filepath.exists():
            return []
        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write_json(self, filename: str, data: list | dict):
        """Atomically write JSON data to file."""
        filepath = self.config_dir / filename
        tmp_path = filepath.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        tmp_path.replace(filepath)

    # --- Cameras ---

    def get_cameras(self) -> list[Camera]:
        """Return all cameras."""
        with self._lock:
            raw = self._read_json("cameras.json")
        return [Camera(**c) for c in raw] if isinstance(raw, list) else []

    def get_camera(self, camera_id: str) -> Camera | None:
        """Return a single camera by ID, or None."""
        for cam in self.get_cameras():
            if cam.id == camera_id:
                return cam
        return None

    def save_camera(self, camera: Camera):
        """Add or update a camera."""
        with self._lock:
            cameras = self._read_json("cameras.json")
            if not isinstance(cameras, list):
                cameras = []
            # Update existing or append
            for i, c in enumerate(cameras):
                if c.get("id") == camera.id:
                    cameras[i] = asdict(camera)
                    self._write_json("cameras.json", cameras)
                    return
            cameras.append(asdict(camera))
            self._write_json("cameras.json", cameras)

    def delete_camera(self, camera_id: str) -> bool:
        """Delete a camera by ID. Returns True if found and deleted."""
        with self._lock:
            cameras = self._read_json("cameras.json")
            if not isinstance(cameras, list):
                return False
            original_len = len(cameras)
            cameras = [c for c in cameras if c.get("id") != camera_id]
            if len(cameras) < original_len:
                self._write_json("cameras.json", cameras)
                return True
            return False

    # --- Users ---

    def get_users(self) -> list[User]:
        """Return all users."""
        with self._lock:
            raw = self._read_json("users.json")
        return [User(**u) for u in raw] if isinstance(raw, list) else []

    def get_user(self, user_id: str) -> User | None:
        """Return a single user by ID, or None."""
        for user in self.get_users():
            if user.id == user_id:
                return user
        return None

    def get_user_by_username(self, username: str) -> User | None:
        """Return a user by username, or None."""
        for user in self.get_users():
            if user.username == username:
                return user
        return None

    def save_user(self, user: User):
        """Add or update a user."""
        with self._lock:
            users = self._read_json("users.json")
            if not isinstance(users, list):
                users = []
            for i, u in enumerate(users):
                if u.get("id") == user.id:
                    users[i] = asdict(user)
                    self._write_json("users.json", users)
                    return
            users.append(asdict(user))
            self._write_json("users.json", users)

    def delete_user(self, user_id: str) -> bool:
        """Delete a user by ID. Returns True if found and deleted."""
        with self._lock:
            users = self._read_json("users.json")
            if not isinstance(users, list):
                return False
            original_len = len(users)
            users = [u for u in users if u.get("id") != user_id]
            if len(users) < original_len:
                self._write_json("users.json", users)
                return True
            return False

    # --- Settings ---

    def get_settings(self) -> Settings:
        """Return system settings, creating defaults if missing."""
        with self._lock:
            raw = self._read_json("settings.json")
        if isinstance(raw, dict) and raw:
            return Settings(**raw)
        return Settings()

    def save_settings(self, settings: Settings):
        """Save system settings."""
        with self._lock:
            self._write_json("settings.json", asdict(settings))

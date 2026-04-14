"""
Tests for application factory helpers: secret key persistence
and default admin user creation.
"""

import os

from monitor import _ensure_default_admin, _load_or_create_secret_key, create_app
from monitor.store import Store


class TestLoadOrCreateSecretKey:
    """Tests for _load_or_create_secret_key()."""

    def test_creates_key_on_first_call(self, tmp_path):
        key = _load_or_create_secret_key(str(tmp_path))
        assert len(key) == 64  # 32 bytes hex = 64 chars
        assert os.path.isfile(tmp_path / ".secret_key")

    def test_returns_same_key_on_second_call(self, tmp_path):
        key1 = _load_or_create_secret_key(str(tmp_path))
        key2 = _load_or_create_secret_key(str(tmp_path))
        assert key1 == key2

    def test_reads_existing_key_file(self, tmp_path):
        key_file = tmp_path / ".secret_key"
        key_file.write_text("abcdef1234567890" * 4)
        key = _load_or_create_secret_key(str(tmp_path))
        assert key == "abcdef1234567890" * 4

    def test_creates_directory_if_missing(self, tmp_path):
        nested = tmp_path / "sub" / "dir"
        key = _load_or_create_secret_key(str(nested))
        assert len(key) == 64
        assert nested.exists()

    def test_ignores_empty_key_file(self, tmp_path):
        key_file = tmp_path / ".secret_key"
        key_file.write_text("")
        key = _load_or_create_secret_key(str(tmp_path))
        assert len(key) == 64  # Generated a new key


class TestEnsureDefaultAdmin:
    """Tests for _ensure_default_admin()."""

    def test_creates_admin_when_no_users(self, tmp_path):
        store = Store(str(tmp_path))
        _ensure_default_admin(store)
        users = store.get_users()
        assert len(users) == 1
        assert users[0].username == "admin"
        assert users[0].role == "admin"
        assert users[0].id == "user-admin-default"

    def test_does_not_create_if_users_exist(self, tmp_path):
        store = Store(str(tmp_path))
        # Create a user first
        from monitor.models import User

        user = User(
            id="user-existing",
            username="existing",
            password_hash="fake",
            role="viewer",
            created_at="2026-01-01T00:00:00Z",
        )
        store.save_user(user)
        _ensure_default_admin(store)
        users = store.get_users()
        assert len(users) == 1
        assert users[0].username == "existing"

    def test_admin_password_is_hashed(self, tmp_path):
        store = Store(str(tmp_path))
        _ensure_default_admin(store)
        users = store.get_users()
        # bcrypt hashes start with $2b$
        assert users[0].password_hash.startswith("$2b$")

    def test_admin_has_created_at(self, tmp_path):
        store = Store(str(tmp_path))
        _ensure_default_admin(store)
        users = store.get_users()
        assert users[0].created_at is not None
        assert "T" in users[0].created_at


class TestCreateApp:
    """Tests for create_app() factory."""

    def test_secret_key_persisted_across_app_instances(self, tmp_path):
        config = {
            "TESTING": True,
            "DATA_DIR": str(tmp_path),
            "RECORDINGS_DIR": str(tmp_path / "recordings"),
            "LIVE_DIR": str(tmp_path / "live"),
            "CONFIG_DIR": str(tmp_path / "config"),
            "CERTS_DIR": str(tmp_path / "certs"),
        }
        for d in ["recordings", "live", "config", "certs", "logs"]:
            (tmp_path / d).mkdir(exist_ok=True)

        app1 = create_app(config=config)
        app2 = create_app(config=config)
        assert app1.config["SECRET_KEY"] == app2.config["SECRET_KEY"]

    def test_test_config_overrides_secret_key(self, tmp_path):
        for d in ["recordings", "live", "config", "certs", "logs"]:
            (tmp_path / d).mkdir(exist_ok=True)
        config = {
            "TESTING": True,
            "DATA_DIR": str(tmp_path),
            "RECORDINGS_DIR": str(tmp_path / "recordings"),
            "LIVE_DIR": str(tmp_path / "live"),
            "CONFIG_DIR": str(tmp_path / "config"),
            "CERTS_DIR": str(tmp_path / "certs"),
            "SECRET_KEY": "test-secret",
        }
        app = create_app(config=config)
        assert app.config["SECRET_KEY"] == "test-secret"

    def test_default_admin_created_on_app_start(self, tmp_path):
        for d in ["recordings", "live", "config", "certs", "logs"]:
            (tmp_path / d).mkdir(exist_ok=True)
        config = {
            "DATA_DIR": str(tmp_path),
            "RECORDINGS_DIR": str(tmp_path / "recordings"),
            "LIVE_DIR": str(tmp_path / "live"),
            "CONFIG_DIR": str(tmp_path / "config"),
            "CERTS_DIR": str(tmp_path / "certs"),
            "SECRET_KEY": "test-secret",
        }
        app = create_app(config=config)
        users = app.store.get_users()
        assert any(u.username == "admin" for u in users)

    def test_default_admin_skipped_in_test_mode(self, tmp_path):
        for d in ["recordings", "live", "config", "certs", "logs"]:
            (tmp_path / d).mkdir(exist_ok=True)
        config = {
            "TESTING": True,
            "DATA_DIR": str(tmp_path),
            "RECORDINGS_DIR": str(tmp_path / "recordings"),
            "LIVE_DIR": str(tmp_path / "live"),
            "CONFIG_DIR": str(tmp_path / "config"),
            "CERTS_DIR": str(tmp_path / "certs"),
            "SECRET_KEY": "test-secret",
        }
        app = create_app(config=config)
        users = app.store.get_users()
        assert not any(u.username == "admin" for u in users)

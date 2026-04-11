"""Tests for the Flask application factory."""


class TestCreateApp:
    """Test the create_app factory function."""

    def test_app_creates_successfully(self, app):
        assert app is not None

    def test_app_is_testing(self, app):
        assert app.config["TESTING"] is True

    def test_app_has_secret_key(self, app):
        assert app.config["SECRET_KEY"] is not None

    def test_app_config_defaults(self, app):
        assert app.config["CLIP_DURATION_SECONDS"] == 180
        assert app.config["STORAGE_THRESHOLD_PERCENT"] == 90
        assert app.config["SESSION_TIMEOUT_MINUTES"] == 30

    def test_app_custom_config(self, data_dir):
        from monitor import create_app

        app = create_app(
            config={
                "TESTING": True,
                "DATA_DIR": str(data_dir),
                "RECORDINGS_DIR": str(data_dir / "recordings"),
                "LIVE_DIR": str(data_dir / "live"),
                "CONFIG_DIR": str(data_dir / "config"),
                "CERTS_DIR": str(data_dir / "certs"),
                "CLIP_DURATION_SECONDS": 300,
            }
        )
        assert app.config["CLIP_DURATION_SECONDS"] == 300

    def test_data_dirs_configured(self, app, data_dir):
        assert app.config["DATA_DIR"] == str(data_dir)
        assert app.config["RECORDINGS_DIR"] == str(data_dir / "recordings")
        assert app.config["LIVE_DIR"] == str(data_dir / "live")
        assert app.config["CONFIG_DIR"] == str(data_dir / "config")
        assert app.config["CERTS_DIR"] == str(data_dir / "certs")


class TestBlueprintRegistration:
    """Test that all API blueprints are registered."""

    def test_auth_blueprint_registered(self, app):
        assert "auth" in app.blueprints

    def test_cameras_blueprint_registered(self, app):
        assert "cameras" in app.blueprints

    def test_recordings_blueprint_registered(self, app):
        assert "recordings" in app.blueprints

    def test_live_blueprint_registered(self, app):
        assert "live" in app.blueprints

    def test_system_blueprint_registered(self, app):
        assert "system" in app.blueprints

    def test_settings_blueprint_registered(self, app):
        assert "settings" in app.blueprints

    def test_users_blueprint_registered(self, app):
        assert "users" in app.blueprints

    def test_ota_blueprint_registered(self, app):
        assert "ota" in app.blueprints

    def test_views_blueprint_registered(self, app):
        assert "views" in app.blueprints

    def test_setup_blueprint_registered(self, app):
        assert "provisioning" in app.blueprints

    def test_all_blueprints_count(self, app):
        """We expect exactly 11 blueprints (9 API + views + setup)."""
        assert len(app.blueprints) == 11

"""Tests for camera test fixtures."""


class TestDataDir:
    """Test that the data_dir fixture creates the right structure."""

    def test_data_dir_exists(self, data_dir):
        assert data_dir.exists()

    def test_config_dir_exists(self, data_dir):
        assert (data_dir / "config").exists()

    def test_certs_dir_exists(self, data_dir):
        assert (data_dir / "certs").exists()

    def test_logs_dir_exists(self, data_dir):
        assert (data_dir / "logs").exists()


class TestCameraConfig:
    """Test the camera_config fixture (returns ConfigManager)."""

    def test_config_loaded(self, camera_config):
        assert camera_config is not None

    def test_config_has_server_ip(self, camera_config):
        assert camera_config.server_ip == "192.168.1.100"

    def test_config_has_camera_id(self, camera_config):
        assert camera_config.camera_id == "cam-test001"

    def test_config_is_configured(self, camera_config):
        assert camera_config.is_configured is True


class TestCameraConfigFile:
    """Test the camera_config_file fixture (returns path)."""

    def test_config_file_exists(self, camera_config_file):
        assert camera_config_file.exists()

    def test_config_file_has_content(self, camera_config_file):
        content = camera_config_file.read_text()
        assert "SERVER_IP=192.168.1.100" in content
        assert "CAMERA_ID=cam-test001" in content


class TestCertsDir:
    """Test the certs_dir fixture."""

    def test_client_cert_exists(self, certs_dir):
        assert (certs_dir / "client.crt").exists()

    def test_client_key_exists(self, certs_dir):
        assert (certs_dir / "client.key").exists()

    def test_ca_cert_exists(self, certs_dir):
        assert (certs_dir / "ca.crt").exists()

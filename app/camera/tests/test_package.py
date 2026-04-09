"""Tests for camera_streamer package structure."""
import camera_streamer


class TestPackage:
    """Test the camera_streamer package is importable."""

    def test_package_imports(self):
        assert camera_streamer is not None

    def test_package_has_docstring(self):
        assert camera_streamer.__doc__ is not None


class TestModuleImports:
    """Test that all modules are importable."""

    def test_import_main(self):
        from camera_streamer import main
        assert main is not None

    def test_import_config(self):
        from camera_streamer import config
        assert config is not None

    def test_import_capture(self):
        from camera_streamer import capture
        assert capture is not None

    def test_import_stream(self):
        from camera_streamer import stream
        assert stream is not None

    def test_import_discovery(self):
        from camera_streamer import discovery
        assert discovery is not None

    def test_import_pairing(self):
        from camera_streamer import pairing
        assert pairing is not None

    def test_import_ota_agent(self):
        from camera_streamer import ota_agent
        assert ota_agent is not None

    def test_import_health(self):
        from camera_streamer import health
        assert health is not None

    def test_import_wifi_setup(self):
        from camera_streamer import wifi_setup
        assert wifi_setup is not None

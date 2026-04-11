"""Tests for camera LUKS key derivation (ADR-0010)."""

from unittest.mock import MagicMock, patch

import pytest

from camera_streamer.encryption import (
    HKDF_INFO,
    KEY_LENGTH,
    EncryptionManager,
    get_cpu_serial,
    hkdf_sha256,
)


class TestHKDFSHA256:
    """Test HKDF-SHA256 implementation against known vectors."""

    def test_output_length(self):
        """Should produce exactly KEY_LENGTH bytes."""
        key = hkdf_sha256(b"secret", b"salt", b"info", KEY_LENGTH)
        assert len(key) == KEY_LENGTH

    def test_deterministic(self):
        """Same inputs should produce same output."""
        key1 = hkdf_sha256(b"secret", b"salt", b"info")
        key2 = hkdf_sha256(b"secret", b"salt", b"info")
        assert key1 == key2

    def test_different_ikm_different_key(self):
        """Different IKM should produce different key."""
        key1 = hkdf_sha256(b"secret1", b"salt", b"info")
        key2 = hkdf_sha256(b"secret2", b"salt", b"info")
        assert key1 != key2

    def test_different_salt_different_key(self):
        """Different salt should produce different key."""
        key1 = hkdf_sha256(b"secret", b"salt1", b"info")
        key2 = hkdf_sha256(b"secret", b"salt2", b"info")
        assert key1 != key2

    def test_different_info_different_key(self):
        """Different info should produce different key."""
        key1 = hkdf_sha256(b"secret", b"salt", b"info1")
        key2 = hkdf_sha256(b"secret", b"salt", b"info2")
        assert key1 != key2

    def test_custom_length(self):
        """Should support custom output lengths."""
        key16 = hkdf_sha256(b"secret", b"salt", b"info", 16)
        key64 = hkdf_sha256(b"secret", b"salt", b"info", 64)
        assert len(key16) == 16
        assert len(key64) == 64

    def test_rfc5869_test_vector(self):
        """Verify against RFC 5869 test vector 1 (SHA-256).

        IKM  = 0x0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b (22 bytes)
        salt = 0x000102030405060708090a0b0c (13 bytes)
        info = 0xf0f1f2f3f4f5f6f7f8f9 (10 bytes)
        L    = 42
        """
        ikm = bytes.fromhex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
        salt = bytes.fromhex("000102030405060708090a0b0c")
        info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
        expected_okm = bytes.fromhex(
            "3cb25f25faacd57a90434f64d0362f2a"
            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"
        )
        okm = hkdf_sha256(ikm, salt, info, 42)
        assert okm == expected_okm


class TestGetCpuSerial:
    """Test CPU serial reading."""

    def test_reads_serial(self):
        """Should extract serial from /proc/cpuinfo format."""
        cpuinfo = "processor\t: 0\nmodel name\t: ARMv7\nSerial\t\t: 100000006789abcd\n"
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda s: iter(cpuinfo.splitlines(True))
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            serial = get_cpu_serial()
        assert serial == "100000006789abcd"

    def test_returns_empty_on_error(self):
        """Should return empty string if /proc/cpuinfo not readable."""
        with patch("builtins.open", side_effect=OSError):
            serial = get_cpu_serial()
        assert serial == ""


class TestEncryptionManager:
    """Test EncryptionManager key derivation."""

    @pytest.fixture
    def pairing_mgr(self):
        mgr = MagicMock()
        # 32 bytes = 64 hex chars
        mgr.get_pairing_secret.return_value = "ab" * 32
        return mgr

    def test_derive_luks_key_success(self, pairing_mgr):
        """Should derive a 32-byte key from pairing_secret + serial."""
        em = EncryptionManager(pairing_mgr, cpu_serial="100000006789abcd")
        key, err = em.derive_luks_key()
        assert key is not None
        assert len(key) == 32
        assert err == ""

    def test_derive_key_deterministic(self, pairing_mgr):
        """Same inputs should always produce same key."""
        em1 = EncryptionManager(pairing_mgr, cpu_serial="100000006789abcd")
        em2 = EncryptionManager(pairing_mgr, cpu_serial="100000006789abcd")
        key1, _ = em1.derive_luks_key()
        key2, _ = em2.derive_luks_key()
        assert key1 == key2

    def test_different_serial_different_key(self, pairing_mgr):
        """Different CPU serials should produce different keys."""
        em1 = EncryptionManager(pairing_mgr, cpu_serial="1111111111111111")
        em2 = EncryptionManager(pairing_mgr, cpu_serial="2222222222222222")
        key1, _ = em1.derive_luks_key()
        key2, _ = em2.derive_luks_key()
        assert key1 != key2

    def test_different_secret_different_key(self):
        """Different pairing secrets should produce different keys."""
        mgr1 = MagicMock()
        mgr1.get_pairing_secret.return_value = "aa" * 32
        mgr2 = MagicMock()
        mgr2.get_pairing_secret.return_value = "bb" * 32

        em1 = EncryptionManager(mgr1, cpu_serial="100000006789abcd")
        em2 = EncryptionManager(mgr2, cpu_serial="100000006789abcd")
        key1, _ = em1.derive_luks_key()
        key2, _ = em2.derive_luks_key()
        assert key1 != key2

    def test_fails_without_pairing_secret(self):
        """Should fail if no pairing secret."""
        mgr = MagicMock()
        mgr.get_pairing_secret.return_value = ""
        em = EncryptionManager(mgr, cpu_serial="100000006789abcd")
        key, err = em.derive_luks_key()
        assert key is None
        assert "not paired" in err

    def test_fails_without_cpu_serial(self, pairing_mgr):
        """Should fail if CPU serial not available."""
        em = EncryptionManager(pairing_mgr, cpu_serial="")
        key, err = em.derive_luks_key()
        assert key is None
        assert "CPU serial" in err

    def test_fails_with_invalid_hex_secret(self):
        """Should fail if pairing secret is not valid hex."""
        mgr = MagicMock()
        mgr.get_pairing_secret.return_value = "not-valid-hex!"
        em = EncryptionManager(mgr, cpu_serial="100000006789abcd")
        key, err = em.derive_luks_key()
        assert key is None
        assert "not hex" in err

    def test_uses_correct_hkdf_info(self, pairing_mgr):
        """Should use the standard info string for key derivation."""
        assert HKDF_INFO == b"home-monitor-camera-luks-v1"

    def test_key_length_constant(self):
        """Key length should be 32 bytes (256-bit)."""
        assert KEY_LENGTH == 32


class TestWriteKeyfile:
    """Test writing LUKS keyfile to disk."""

    @pytest.fixture
    def pairing_mgr(self):
        mgr = MagicMock()
        mgr.get_pairing_secret.return_value = "ab" * 32
        return mgr

    def test_writes_keyfile(self, pairing_mgr, tmp_path):
        """Should write derived key to file."""
        em = EncryptionManager(pairing_mgr, cpu_serial="100000006789abcd")
        keyfile = tmp_path / "keydir" / "data.key"
        ok, err = em.write_keyfile(str(keyfile))
        assert ok is True
        assert err == ""
        assert keyfile.exists()
        assert len(keyfile.read_bytes()) == 32

    def test_keyfile_contents_match_derived_key(self, pairing_mgr, tmp_path):
        """Keyfile contents should match derive_luks_key output."""
        em = EncryptionManager(pairing_mgr, cpu_serial="100000006789abcd")
        key, _ = em.derive_luks_key()
        keyfile = tmp_path / "data.key"
        em.write_keyfile(str(keyfile))
        assert keyfile.read_bytes() == key

    def test_fails_without_secret(self, tmp_path):
        """Should fail if no pairing secret."""
        mgr = MagicMock()
        mgr.get_pairing_secret.return_value = ""
        em = EncryptionManager(mgr, cpu_serial="100000006789abcd")
        ok, err = em.write_keyfile(str(tmp_path / "data.key"))
        assert ok is False
        assert "not paired" in err

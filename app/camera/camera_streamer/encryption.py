"""
LUKS key derivation for camera data encryption (ADR-0010).

Derives a LUKS key from the pairing secret (ADR-0009) and CPU serial.
Uses HKDF-SHA256 — no external dependencies beyond Python stdlib.

The derived key is used to:
1. Format /data with LUKS2 + Adiantum on first boot
2. Auto-unlock /data on subsequent boots (keyfile in initramfs)

Key derivation:
  camera_luks_key = HKDF-SHA256(
      ikm  = pairing_secret,
      salt = camera_cpu_serial,
      info = "home-monitor-camera-luks-v1"
  )
"""

import hashlib
import hmac
import logging
import os

log = logging.getLogger("camera-streamer.encryption")

HKDF_INFO = b"home-monitor-camera-luks-v1"
KEY_LENGTH = 32  # 256-bit key


def _hkdf_extract(salt, ikm):
    """HKDF-Extract: PRK = HMAC-SHA256(salt, IKM)."""
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk, info, length):
    """HKDF-Expand: derive output key material from PRK."""
    hash_len = 32  # SHA-256 output length
    n = (length + hash_len - 1) // hash_len
    okm = b""
    t = b""
    for i in range(1, n + 1):
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
    return okm[:length]


def hkdf_sha256(ikm, salt, info, length=KEY_LENGTH):
    """HKDF-SHA256 key derivation (RFC 5869).

    Args:
        ikm: Input keying material (bytes).
        salt: Salt value (bytes).
        info: Context/application info (bytes).
        length: Output key length in bytes.

    Returns:
        Derived key as bytes.
    """
    prk = _hkdf_extract(salt, ikm)
    return _hkdf_expand(prk, info, length)


def get_cpu_serial():
    """Read the RPi CPU serial from /proc/cpuinfo.

    Returns:
        CPU serial string, or empty string if not found.
    """
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":")[-1].strip()
    except OSError:
        pass
    return ""


class EncryptionManager:
    """Manages LUKS key derivation for camera data encryption.

    Args:
        pairing_manager: PairingManager instance (to read pairing_secret).
        cpu_serial: CPU serial override (for testing). If None, reads from /proc/cpuinfo.
    """

    def __init__(self, pairing_manager, cpu_serial=None):
        self._pairing = pairing_manager
        self._cpu_serial = cpu_serial

    @property
    def cpu_serial(self):
        """Return the CPU serial (cached after first read)."""
        if self._cpu_serial is None:
            self._cpu_serial = get_cpu_serial()
        return self._cpu_serial

    def derive_luks_key(self):
        """Derive the LUKS key from pairing_secret + CPU serial.

        Returns:
            (key_bytes, error) tuple. key_bytes is 32 bytes on success,
            None on failure.
        """
        secret_hex = self._pairing.get_pairing_secret()
        if not secret_hex:
            return None, "No pairing secret — camera not paired"

        serial = self.cpu_serial
        if not serial:
            return None, "Cannot read CPU serial from /proc/cpuinfo"

        try:
            ikm = bytes.fromhex(secret_hex)
        except ValueError:
            return None, "Invalid pairing secret format (not hex)"

        salt = serial.encode("utf-8")
        key = hkdf_sha256(ikm, salt, HKDF_INFO, KEY_LENGTH)

        log.info(
            "LUKS key derived (serial=%s...%s, key_len=%d)",
            serial[:4],
            serial[-4:],
            len(key),
        )
        return key, ""

    def write_keyfile(self, path):
        """Derive LUKS key and write to a keyfile.

        Args:
            path: Path to write the keyfile (e.g., /etc/cryptsetup-keys.d/data.key).

        Returns:
            (success, error) tuple.
        """
        key, err = self.derive_luks_key()
        if key is None:
            return False, err

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(key)
            os.chmod(path, 0o400)
            log.info("LUKS keyfile written to %s", path)
            return True, ""
        except OSError as e:
            log.error("Failed to write keyfile: %s", e)
            return False, f"Failed to write keyfile: {e}"

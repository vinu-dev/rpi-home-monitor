"""Property-based tests for camera cryptographic primitives (camera_streamer.encryption).

Properties under test:
  - hkdf_sha256: deterministic — same inputs always yield the same key.
  - hkdf_sha256: output length is exactly as requested.
  - hkdf_sha256: different IKM produces different keys (collision resistance).
  - hkdf_sha256: different salt produces different keys.
  - hkdf_sha256: different info produces different keys.
  - _hkdf_extract: output is always 32 bytes (SHA-256 PRK).
  - _hkdf_expand: honours requested length.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from camera_streamer.encryption import (
    KEY_LENGTH,
    _hkdf_expand,
    _hkdf_extract,
    hkdf_sha256,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_bytes_nonempty = st.binary(min_size=1, max_size=64)
_key_lengths = st.integers(min_value=1, max_value=128)

# Pairs of distinct byte strings
_distinct_bytes = st.tuples(_bytes_nonempty, _bytes_nonempty).filter(
    lambda p: p[0] != p[1]
)


# ---------------------------------------------------------------------------
# hkdf_sha256 — full function
# ---------------------------------------------------------------------------


class TestHkdfSha256:
    @given(ikm=_bytes_nonempty, salt=_bytes_nonempty, info=_bytes_nonempty)
    def test_deterministic(self, ikm, salt, info):
        """Same inputs must always produce identical output."""
        k1 = hkdf_sha256(ikm, salt, info)
        k2 = hkdf_sha256(ikm, salt, info)
        assert k1 == k2

    @given(
        ikm=_bytes_nonempty,
        salt=_bytes_nonempty,
        info=_bytes_nonempty,
        length=_key_lengths,
    )
    def test_output_length_matches_request(self, ikm, salt, info, length):
        key = hkdf_sha256(ikm, salt, info, length=length)
        assert len(key) == length

    def test_default_length_is_key_length_constant(self):
        key = hkdf_sha256(b"ikm", b"salt", b"info")
        assert len(key) == KEY_LENGTH

    @given(pair=_distinct_bytes, salt=_bytes_nonempty, info=_bytes_nonempty)
    def test_different_ikm_produces_different_key(self, pair, salt, info):
        ikm_a, ikm_b = pair
        assert hkdf_sha256(ikm_a, salt, info) != hkdf_sha256(ikm_b, salt, info)

    @given(ikm=_bytes_nonempty, pair=_distinct_bytes, info=_bytes_nonempty)
    def test_different_salt_produces_different_key(self, ikm, pair, info):
        salt_a, salt_b = pair
        assert hkdf_sha256(ikm, salt_a, info) != hkdf_sha256(ikm, salt_b, info)

    @given(ikm=_bytes_nonempty, salt=_bytes_nonempty, pair=_distinct_bytes)
    def test_different_info_produces_different_key(self, ikm, salt, pair):
        info_a, info_b = pair
        assert hkdf_sha256(ikm, salt, info_a) != hkdf_sha256(ikm, salt, info_b)

    @given(ikm=_bytes_nonempty, salt=_bytes_nonempty, info=_bytes_nonempty)
    def test_output_is_bytes(self, ikm, salt, info):
        key = hkdf_sha256(ikm, salt, info)
        assert isinstance(key, bytes)

    def test_known_vector(self):
        """Regression: known-good output must not silently change."""
        ikm = bytes.fromhex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
        salt = bytes.fromhex("000102030405060708090a0b0c")
        info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
        key = hkdf_sha256(ikm, salt, info, length=42)
        # RFC 5869 Test Case 1 OKM
        expected = bytes.fromhex(
            "3cb25f25faacd57a90434f64d0362f2a"
            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"
        )
        assert key == expected


# ---------------------------------------------------------------------------
# _hkdf_extract internals
# ---------------------------------------------------------------------------


class TestHkdfExtract:
    @given(salt=_bytes_nonempty, ikm=_bytes_nonempty)
    def test_prk_is_always_32_bytes(self, salt, ikm):
        prk = _hkdf_extract(salt, ikm)
        assert len(prk) == 32

    @given(salt=_bytes_nonempty, ikm=_bytes_nonempty)
    def test_prk_is_bytes(self, salt, ikm):
        assert isinstance(_hkdf_extract(salt, ikm), bytes)

    @given(salt=_bytes_nonempty, pair=_distinct_bytes)
    def test_different_ikm_changes_prk(self, salt, pair):
        a, b = pair
        assert _hkdf_extract(salt, a) != _hkdf_extract(salt, b)


# ---------------------------------------------------------------------------
# _hkdf_expand internals
# ---------------------------------------------------------------------------


class TestHkdfExpand:
    @given(
        prk=st.binary(min_size=32, max_size=32),
        info=_bytes_nonempty,
        length=_key_lengths,
    )
    def test_output_length_matches_request(self, prk, info, length):
        okm = _hkdf_expand(prk, info, length)
        assert len(okm) == length

    @given(
        prk=st.binary(min_size=32, max_size=32),
        info=_bytes_nonempty,
        length=_key_lengths,
    )
    def test_deterministic(self, prk, info, length):
        assert _hkdf_expand(prk, info, length) == _hkdf_expand(prk, info, length)

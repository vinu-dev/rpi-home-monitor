# REQ: SWR-016, SWR-046; RISK: RISK-004, RISK-019; SEC: SC-018; TEST: TC-013, TC-043
"""Unit tests for the camera-side release_version() helper.

The helper is the single image-side SSOT for the product release
version (per docs/architecture/versioning.md §C). Server has a
byte-identical copy under monitor.release_version; the static
guard scripts/check_versioning_design.py asserts they don't
diverge.

These tests exercise parsing — quoted/unquoted, missing files,
malformed lines, the cache, and the reset hatch.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from camera_streamer import release_version as rv_module
from camera_streamer.release_version import (
    _clear_cache,
    release_version,
)


@pytest.fixture(autouse=True)
def reset_cache():
    _clear_cache()
    yield
    _clear_cache()


def _write(p: Path, contents: str) -> Path:
    p.write_text(textwrap.dedent(contents).lstrip("\n"), encoding="utf-8")
    return p


class TestParse:
    def test_double_quoted_value(self, tmp_path):
        p = _write(
            tmp_path / "os-release",
            """
            NAME="Home Monitor OS"
            VERSION_ID="1.4.3"
            PRETTY_NAME="Home Monitor OS 1.4.3"
            """,
        )
        assert release_version(str(p)) == "1.4.3"

    def test_single_quoted_value(self, tmp_path):
        p = _write(tmp_path / "os-release", "VERSION_ID='1.4.3'\n")
        assert release_version(str(p)) == "1.4.3"

    def test_unquoted_value(self, tmp_path):
        # os-release(5) allows unquoted values for ASCII-only strings.
        p = _write(tmp_path / "os-release", "VERSION_ID=1.4.3\n")
        assert release_version(str(p)) == "1.4.3"

    def test_pre_release_suffix(self, tmp_path):
        p = _write(tmp_path / "os-release", 'VERSION_ID="1.5.0-rc1"\n')
        assert release_version(str(p)) == "1.5.0-rc1"

    def test_missing_file(self, tmp_path):
        # Returns "" rather than raising — callers render "unknown".
        assert release_version(str(tmp_path / "does-not-exist")) == ""

    def test_missing_version_id_field(self, tmp_path):
        p = _write(tmp_path / "os-release", 'NAME="Other"\n')
        assert release_version(str(p)) == ""

    def test_blank_lines_and_comments_tolerated(self, tmp_path):
        p = _write(
            tmp_path / "os-release",
            """

            # this is a comment
            NAME="Home Monitor OS"

            VERSION_ID="1.4.3"
            """,
        )
        assert release_version(str(p)) == "1.4.3"

    def test_malformed_lines_skipped(self, tmp_path):
        # Line without `=` shouldn't crash the parser.
        p = _write(
            tmp_path / "os-release",
            """
            this is not a valid line
            VERSION_ID="1.4.3"
            another bad line
            """,
        )
        assert release_version(str(p)) == "1.4.3"

    def test_first_match_wins(self, tmp_path):
        # If somehow VERSION_ID appears twice, return the first.
        p = _write(
            tmp_path / "os-release",
            """
            VERSION_ID="1.4.3"
            VERSION_ID="should-be-ignored"
            """,
        )
        assert release_version(str(p)) == "1.4.3"


class TestCache:
    def test_cached_after_first_real_read(self, tmp_path, monkeypatch):
        """release_version() with no path arg uses the cache + module
        constant. Patch the constant to point at a tmp file, call
        once, mutate the file, call again — cached value persists."""
        p = _write(tmp_path / "os-release", 'VERSION_ID="1.4.3"\n')
        monkeypatch.setattr(rv_module, "_OS_RELEASE_PATH", str(p))
        first = release_version()
        assert first == "1.4.3"

        # Mutate file under us; cache should hide the change.
        p.write_text('VERSION_ID="9.9.9"\n', encoding="utf-8")
        assert release_version() == "1.4.3"

    def test_clear_cache_lets_a_fresh_read_through(self, tmp_path, monkeypatch):
        p = _write(tmp_path / "os-release", 'VERSION_ID="1.4.3"\n')
        monkeypatch.setattr(rv_module, "_OS_RELEASE_PATH", str(p))
        assert release_version() == "1.4.3"
        p.write_text('VERSION_ID="9.9.9"\n', encoding="utf-8")
        _clear_cache()
        assert release_version() == "9.9.9"

    def test_explicit_path_bypasses_cache(self, tmp_path):
        a = _write(tmp_path / "a", 'VERSION_ID="1.0.0"\n')
        b = _write(tmp_path / "b", 'VERSION_ID="2.0.0"\n')
        # Explicit-path mode is uncached on purpose so tests can
        # interleave reads against different files in one run.
        assert release_version(str(a)) == "1.0.0"
        assert release_version(str(b)) == "2.0.0"
        assert release_version(str(a)) == "1.0.0"

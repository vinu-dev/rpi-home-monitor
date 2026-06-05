from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
BUILD_SH = REPO_ROOT / "scripts" / "build.sh"


def test_build_script_stamps_image_and_swu_from_same_version():
    text = BUILD_SH.read_text(encoding="utf-8")

    assert 'BUILD_VERSION="$(resolve_build_version)"' in text
    assert 'version="$BUILD_VERSION"' in text
    assert 'local image_version="${version#v}"' in text
    assert 'HOME_MONITOR_BUILD_VERSION = "$image_version"' in text

    image_override_pos = text.index(
        'stage_image_version_override "$builddir" "$BUILD_VERSION"'
    )
    bitbake_pos = text.index('bitbake "$image"')
    assert image_override_pos < bitbake_pos


def test_distro_conf_consumes_generated_build_version_before_version_file():
    distro_conf = (
        REPO_ROOT / "meta-home-monitor" / "conf" / "distro" / "home-monitor.conf"
    )
    text = distro_conf.read_text(encoding="utf-8")

    assert 'HOME_MONITOR_BUILD_VERSION ??= ""' in text
    assert "d.getVar('HOME_MONITOR_BUILD_VERSION') or open(" in text
    assert "../VERSION" in text

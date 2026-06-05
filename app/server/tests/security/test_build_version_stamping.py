from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
BUILD_SH = REPO_ROOT / "scripts" / "build.sh"


def test_build_script_stamps_image_and_swu_from_same_version():
    text = BUILD_SH.read_text(encoding="utf-8")

    assert 'BUILD_VERSION="$(resolve_build_version)"' in text
    assert 'version="$BUILD_VERSION"' in text
    assert 'local image_version="${version#v}"' in text
    assert 'DISTRO_VERSION = "$image_version"' in text
    assert 'BUILD_ID = "$image_version"' in text

    image_override_pos = text.index(
        'stage_image_version_override "$builddir" "$BUILD_VERSION"'
    )
    bitbake_pos = text.index('bitbake "$image"')
    assert image_override_pos < bitbake_pos

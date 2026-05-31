# REQ: SWR-038; RISK: RISK-004; SEC: SC-003; TEST: TC-036
"""Unit tests for the server-side reusable camera OTA library."""

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from monitor.api import ota as ota_api


def _ota(tmp_path):
    inbox = tmp_path / "inbox"
    library = tmp_path / "camera-library"
    inbox.mkdir()
    library.mkdir()
    return SimpleNamespace(
        inbox_dir=str(inbox),
        camera_staging_dir=str(library),
    )


def test_read_camera_bundle_records_filters_invalid_manifests(tmp_path):
    ota = _ota(tmp_path)
    library = tmp_path / "camera-library"
    bundle = library / "camera.swu"
    bundle.write_bytes(b"bundle")
    (library / "valid.json").write_text(
        json.dumps(
            {
                "target": "camera",
                "filename": "camera.swu",
                "target_version": "1.6.2",
            }
        ),
        encoding="utf-8",
    )
    (library / "bad-json.json").write_text("{bad", encoding="utf-8")
    (library / "server.json").write_text(
        json.dumps({"target": "server", "filename": "server.swu"}),
        encoding="utf-8",
    )
    (library / "not-swu.json").write_text(
        json.dumps({"target": "camera", "filename": "camera.txt"}),
        encoding="utf-8",
    )
    (library / "missing.json").write_text(
        json.dumps({"target": "camera", "filename": "missing.swu"}),
        encoding="utf-8",
    )

    records = ota_api._read_camera_bundle_records(ota)

    assert len(records) == 1
    assert records[0]["filename"] == "camera.swu"
    assert records[0]["path"] == str(bundle)


def test_read_camera_bundle_records_handles_missing_library(tmp_path):
    ota = SimpleNamespace(camera_staging_dir=str(tmp_path / "missing"))

    assert ota_api._read_camera_bundle_records(ota) == []


def test_write_camera_bundle_record_is_atomic_and_records_version(tmp_path):
    ota = _ota(tmp_path)
    bundle = tmp_path / "camera-library" / "stored-camera.swu"
    bundle.write_bytes(b"bundle")

    with (
        patch(
            "monitor.api.ota.ota_service.extract_bundle_version", return_value="1.6.2"
        ),
        patch("monitor.api.ota.time.time", return_value=123.0),
    ):
        record = ota_api._write_camera_bundle_record(
            ota,
            str(bundle),
            "camera-update.swu",
            "a" * 64,
        )

    assert record["target"] == "camera"
    assert record["filename"] == "stored-camera.swu"
    assert record["original_filename"] == "camera-update.swu"
    assert record["target_version"] == "1.6.2"
    manifest = tmp_path / "camera-library" / f"{'a' * 64}.json"
    assert json.loads(manifest.read_text(encoding="utf-8")) == record


def test_best_camera_bundle_skips_same_and_downgrade_and_picks_newest_upgrade():
    camera = SimpleNamespace(firmware_version="1.6.0")
    older = {"target_version": "1.5.0", "uploaded_at": 300}
    same = {"target_version": "1.6.0", "uploaded_at": 400}
    first = {"target_version": "1.6.1", "uploaded_at": 100}
    better = {"target_version": "1.6.2", "uploaded_at": 50}

    best, decision = ota_api._best_camera_bundle_for(
        camera,
        [older, same, first, better],
    )

    assert best is better
    assert decision.relation == "upgrade"


def test_bundle_ordering_uses_upload_time_when_versions_match():
    current = {"target_version": "1.6.1", "uploaded_at": 100}
    candidate = {"target_version": "1.6.1", "uploaded_at": 200}

    assert ota_api._is_better_camera_bundle(candidate, current) is True
    assert ota_api._is_better_camera_bundle(current, candidate) is False


def test_discard_legacy_camera_inbox_ignores_remove_errors(tmp_path):
    ota = _ota(tmp_path)

    with patch("monitor.api.ota.shutil.rmtree", side_effect=OSError("busy")) as rmtree:
        ota_api._discard_legacy_camera_inbox(ota, "cam-001/../bad")

    called_path = rmtree.call_args.args[0]
    assert os.path.basename(called_path) == "camera-cam-001bad"

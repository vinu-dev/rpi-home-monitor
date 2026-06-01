# REQ: SWR-010, SWR-038; RISK: RISK-004, RISK-019; SEC: SC-003, SC-018; TEST: TC-009, TC-036
"""
Over-the-Air update API (ADR-0008, ADR-0020).

Endpoints:
  GET  /ota/status                 - per-device OTA status (server + cameras)
  POST /ota/server/upload          - upload .swu for server (admin, multipart)
  POST /ota/server/install         - install staged server bundle (admin)
  POST /ota/camera-library/upload  - upload reusable camera .swu (admin)
  POST /ota/camera/<id>/upload     - legacy reusable camera upload (admin)
  POST /ota/camera/<id>/custom-upload - one-shot camera .swu (admin)
  POST /ota/camera/<id>/custom-discard - discard one-shot camera .swu
  POST /ota/camera/<id>/push       - stream bundle to camera via mTLS (admin)
  POST /ota/cameras/push           - stream reusable bundle to eligible cameras
  GET  /ota/usb/scan               - scan mounted USB for bundles (admin)
  POST /ota/usb/import             - import + stage bundle from USB (admin)

The camera path is dual-transport (ADR-0020): the user uploads a .swu
to the server through the Settings GUI; the server then relays it to
the camera's OTA agent via mTLS. The camera verifies the signature
and invokes swupdate exactly as it would for a direct upload — so
the install layer is identical on both devices.
"""

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time

from flask import Blueprint, current_app, jsonify, request, session

from monitor import ota_policy
from monitor.auth import admin_required, csrf_protect, login_required
from monitor.release_version import release_version
from monitor.services import ota_service

ota_bp = Blueprint("ota", __name__)

# How often the background push task refreshes the camera's live
# status into the server-side tracker (ota_service.set_status). Short
# enough to feel responsive in the UI without hammering the camera.
CAMERA_STATUS_POLL_SECONDS = 2.0
CAMERA_BUSY_STATES = {
    "uploading",
    "downloading",
    "verifying",
    "installing",
    "rebooting",
    "validating",
}
SERVER_INSTALL_ACTIVE_STATES = {
    "verifying",
    "installing",
    "rebooting",
}
CAMERA_LIBRARY_MANIFEST_SUFFIX = ".json"
CAMERA_CUSTOM_MANIFEST = "manifest.json"


def _normal_state(status):
    return str((status or {}).get("state") or "idle").lower()


def _active_camera_update(ota):
    """Return the first camera update that would conflict with server install."""
    operation = ota.get_operation()
    if operation.get("kind") == ota_service.CAMERA_UPDATE_OPERATION:
        device_ids = operation.get("device_ids") or []
        if device_ids:
            return device_ids[0], "reserved"
        return "camera-update", "reserved"
    for camera in current_app.store.get_cameras():
        if camera.status == "pending":
            continue
        state = _normal_state(ota.get_status(camera.id))
        if state in CAMERA_BUSY_STATES:
            return camera.id, state
    return "", ""


def _reject_if_camera_update_active(ota):
    camera_id, state = _active_camera_update(ota)
    if not camera_id:
        return None
    return (
        jsonify(
            {
                "error": (
                    "Camera update is already running "
                    f"({camera_id}: {state}). Wait for camera updates to "
                    "finish before installing a server update."
                )
            }
        ),
        409,
    )


def _reject_if_server_install_active(ota):
    operation = ota.get_operation()
    if operation.get("kind") == ota_service.SERVER_INSTALL_OPERATION:
        return (
            jsonify(
                {
                    "error": (
                        "Server update is already running. Wait for the server "
                        "update to finish before starting camera updates."
                    )
                }
            ),
            409,
        )
    state = _normal_state(ota.get_status("server"))
    if state not in SERVER_INSTALL_ACTIVE_STATES:
        return None
    return (
        jsonify(
            {
                "error": (
                    "Server update is already running "
                    f"({state}). Wait for the server update to finish before "
                    "starting camera updates."
                )
            }
        ),
        409,
    )


def _safe_camera_id(camera_id):
    return "".join(c for c in camera_id if c.isalnum() or c in ("-", "_"))


def _camera_inbox_dir(ota, camera_id):
    """Legacy per-camera inbox directory retained for cleanup only."""
    return os.path.join(ota.inbox_dir, f"camera-{_safe_camera_id(camera_id)}")


def _camera_library_dir(ota):
    return ota.camera_staging_dir


def _camera_custom_root(ota):
    custom_dir = getattr(ota, "camera_custom_dir", "")
    if custom_dir:
        return custom_dir
    return os.path.join(ota.ota_dir, "camera-custom")


def _camera_custom_dir(ota, camera_id):
    return os.path.join(_camera_custom_root(ota), _safe_camera_id(camera_id))


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_camera_bundle_records(ota):
    """Return valid reusable camera OTA library records.

    Loose files are ignored by design. A bundle becomes offerable only after
    the camera upload endpoint validates it and writes a sidecar manifest.
    """
    library = _camera_library_dir(ota)
    try:
        entries = list(os.scandir(library))
    except OSError:
        return []

    records = []
    for entry in entries:
        if not entry.is_file() or not entry.name.endswith(
            CAMERA_LIBRARY_MANIFEST_SUFFIX
        ):
            continue
        try:
            with open(entry.path, encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("target") != "camera":
            continue
        filename = os.path.basename(str(record.get("filename") or ""))
        if not filename.lower().endswith(".swu"):
            continue
        bundle_path = os.path.join(library, filename)
        if not os.path.isfile(bundle_path):
            continue
        record["path"] = bundle_path
        record["filename"] = filename
        records.append(record)
    return records


def _is_better_camera_bundle(candidate, current):
    if current is None:
        return True
    ordering = ota_policy.compare_versions(
        str(candidate.get("target_version") or ""),
        str(current.get("target_version") or ""),
    )
    if ordering is not None and ordering != 0:
        return ordering > 0
    return float(candidate.get("uploaded_at") or 0) > float(
        current.get("uploaded_at") or 0
    )


def _best_camera_bundle_for(camera, records):
    best = None
    best_decision = None
    for record in records:
        target_version = str(record.get("target_version") or "")
        decision = ota_policy.classify_update(camera.firmware_version, target_version)
        if decision.blocked or decision.relation == "same":
            continue
        if _is_better_camera_bundle(record, best):
            best = record
            best_decision = decision
    return best, best_decision


def _write_camera_bundle_record(ota, bundle_path, original_filename, sha256):
    library = _camera_library_dir(ota)
    record = {
        "target": "camera",
        "filename": os.path.basename(bundle_path),
        "original_filename": original_filename,
        "sha256": sha256,
        "target_version": ota_service.extract_bundle_version(bundle_path),
        "uploaded_at": time.time(),
    }
    manifest_path = os.path.join(library, f"{sha256}{CAMERA_LIBRARY_MANIFEST_SUFFIX}")
    tmp_path = f"{manifest_path}.partial-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(record, f, sort_keys=True)
    os.replace(tmp_path, manifest_path)
    return record


def _latest_camera_bundle(records):
    latest = None
    for record in records:
        if _is_better_camera_bundle(record, latest):
            latest = record
    return latest


def _camera_bundle_summary(record, cameras):
    if not record:
        return {
            "filename": "",
            "target_version": "",
            "uploaded_at": 0,
            "eligible_count": 0,
            "already_current_count": 0,
            "offline_count": 0,
            "blocked_count": 0,
            "total_count": len([c for c in cameras if c.status != "pending"]),
        }

    target_version = str(record.get("target_version") or "")
    summary = {
        "filename": record.get("original_filename") or record["filename"],
        "target_version": target_version,
        "uploaded_at": record.get("uploaded_at") or 0,
        "eligible_count": 0,
        "already_current_count": 0,
        "offline_count": 0,
        "blocked_count": 0,
        "total_count": 0,
    }
    for camera in cameras:
        if camera.status == "pending":
            continue
        summary["total_count"] += 1
        decision = ota_policy.classify_update(camera.firmware_version, target_version)
        if decision.blocked:
            summary["blocked_count"] += 1
        elif decision.relation == "same":
            summary["already_current_count"] += 1
        elif camera.status != "online":
            summary["offline_count"] += 1
        else:
            summary["eligible_count"] += 1
    return summary


def _read_camera_custom_record(ota, camera_id):
    camera_dir = _camera_custom_dir(ota, camera_id)
    manifest_path = os.path.join(camera_dir, CAMERA_CUSTOM_MANIFEST)
    try:
        with open(manifest_path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("target") != "camera":
        return None
    filename = os.path.basename(str(record.get("filename") or ""))
    if not filename.lower().endswith(".swu"):
        return None
    bundle_path = os.path.join(camera_dir, filename)
    if not os.path.isfile(bundle_path):
        return None
    record["path"] = bundle_path
    record["filename"] = filename
    record["bundle_scope"] = "custom"
    return record


def _write_camera_custom_record(ota, camera_id, bundle_path, original_filename, sha256):
    camera_dir = _camera_custom_dir(ota, camera_id)
    record = {
        "target": "camera",
        "bundle_scope": "custom",
        "filename": os.path.basename(bundle_path),
        "original_filename": original_filename,
        "sha256": sha256,
        "target_version": ota_service.extract_bundle_version(bundle_path),
        "uploaded_at": time.time(),
    }
    manifest_path = os.path.join(camera_dir, CAMERA_CUSTOM_MANIFEST)
    tmp_path = f"{manifest_path}.partial-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(record, f, sort_keys=True)
    os.replace(tmp_path, manifest_path)
    return record


def _discard_camera_custom_record(ota, camera_id):
    try:
        shutil.rmtree(_camera_custom_dir(ota, camera_id), ignore_errors=True)
    except OSError:
        pass


def _discard_legacy_camera_inbox(ota, camera_id):
    """Remove pre-library per-camera staging left by older server builds."""
    try:
        shutil.rmtree(_camera_inbox_dir(ota, camera_id), ignore_errors=True)
    except OSError:
        pass


def _store_camera_library_upload(ota, file, filename):
    library = _camera_library_dir(ota)
    fd, tmp_path = tempfile.mkstemp(suffix=".swu", dir=library)
    try:
        with os.fdopen(fd, "wb") as target_file:
            file.save(target_file)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None, 0, f"Upload failed: {exc}"

    try:
        size = os.path.getsize(tmp_path)
    except OSError:
        size = 0
    if size == 0:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None, 0, "Uploaded file is empty"

    target = ota_service.extract_bundle_target(tmp_path)
    if target != "camera":
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if target == "server":
            return None, size, "This is a server bundle; upload a camera bundle"
        return None, size, "This bundle target is not recognized as a camera bundle"

    sha256 = _hash_file(tmp_path)
    stored_filename = f"{sha256[:12]}-{filename}"
    target_path = os.path.join(library, stored_filename)
    try:
        os.replace(tmp_path, target_path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None, size, f"Upload failed: {exc}"
    return _write_camera_bundle_record(ota, target_path, filename, sha256), size, ""


@ota_bp.route("/status", methods=["GET"])
@login_required
def get_status():
    """Get OTA update status for server + all cameras."""
    cameras = current_app.store.get_cameras()
    ota = current_app.ota_service
    ota.ensure_storage()
    camera_records = _read_camera_bundle_records(ota)
    common_record = _latest_camera_bundle(camera_records)

    # Live read from /etc/os-release VERSION_ID, not the persisted
    # Settings.firmware_version (which is legacy plumbing — see
    # docs/architecture/versioning.md §C).
    current_server_version = release_version()
    server_status = {
        "current_version": current_server_version,
        **ota.get_status("server", current_version=current_server_version),
    }
    server_status["verification"] = ota.get_verification_posture()

    result = {
        "server": server_status,
        "camera_bundle": _camera_bundle_summary(common_record, cameras),
        "cameras": [],
        "operation": ota.get_operation(),
    }

    for cam in cameras:
        if cam.status == "pending":
            continue
        _discard_legacy_camera_inbox(ota, cam.id)
        raw_status = ota.get_status(cam.id)
        entry = {
            "id": cam.id,
            "name": cam.name,
            "online": cam.status == "online",
            "current_version": cam.firmware_version,
            **raw_status,
        }

        state = entry.get("state", "idle")
        if state in CAMERA_BUSY_STATES:
            result["cameras"].append(entry)
            continue

        status_target = str(entry.get("target_version") or "")
        installed_current = state == "installed" and ota_policy.versions_match(
            cam.firmware_version, status_target
        )
        if installed_current:
            if entry.get("bundle_scope") == "custom":
                _discard_camera_custom_record(ota, cam.id)
            ota.set_status(
                cam.id,
                "idle",
                progress=0,
                error="",
                staged_filename="",
                target_version="",
                update_relation="",
                bundle_scope="",
            )
            entry.update(
                {
                    "state": "idle",
                    "progress": 0,
                    "error": "",
                    "staged_filename": "",
                    "target_version": "",
                    "update_relation": "",
                    "bundle_scope": "",
                }
            )

        custom = _read_camera_custom_record(ota, cam.id)
        if custom and state != "installed":
            target_version = str(custom.get("target_version") or "")
            decision = ota_policy.classify_update(cam.firmware_version, target_version)
            if decision.blocked or decision.relation == "same":
                _discard_camera_custom_record(ota, cam.id)
                custom = None
            else:
                if state not in {"error", "installed"}:
                    entry["state"] = "staged"
                entry["bundle_scope"] = "custom"
                entry["staged_filename"] = (
                    custom.get("original_filename") or custom["filename"]
                )
                entry["target_version"] = target_version
                entry["update_relation"] = decision.relation

        if not custom:
            if common_record:
                target_version = str(common_record.get("target_version") or "")
                decision = ota_policy.classify_update(
                    cam.firmware_version, target_version
                )
                filename = (
                    common_record.get("original_filename") or common_record["filename"]
                )
                entry["bundle_scope"] = "common"
                entry["staged_filename"] = filename
                entry["target_version"] = target_version
                entry["update_relation"] = (
                    "blocked" if decision.blocked else decision.relation
                )
                if (
                    not decision.blocked
                    and decision.relation != "same"
                    and state not in {"error", "installed"}
                ):
                    entry["state"] = "staged"
            else:
                entry["staged_filename"] = ""
                entry["target_version"] = ""
                entry["update_relation"] = ""
                entry["bundle_scope"] = ""
                if state == "staged":
                    entry["state"] = "idle"
        if entry.get("update_relation") == "same" and entry.get("state") == "staged":
            entry["state"] = "idle"
        if entry.get("update_relation") == "blocked" and entry.get("state") == "staged":
            entry["state"] = "idle"
        result["cameras"].append(entry)

    return jsonify(result), 200


@ota_bp.route("/server/upload", methods=["POST"])
@admin_required
@csrf_protect
def upload_server_image():
    """Upload a .swu image for server OTA update. Admin only."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    ota = current_app.ota_service
    user = session.get("username", "")
    ip = request.remote_addr or ""
    filename = ota_service.sanitize_bundle_filename(file.filename)
    if not filename:
        return jsonify({"error": "Only .swu files are accepted"}), 400

    # Reject concurrent uploads — two admins racing to upload different
    # bundles would stage both into the same dir and the install
    # endpoint would non-deterministically pick one. Camera has had
    # this guard since the original design; server is catching up.
    if ota.is_busy("server"):
        state = ota.get_status("server").get("state")
        return jsonify(
            {"error": f"A server update is already in progress ({state})"}
        ), 409

    try:
        ok, storage_err = ota.ensure_storage()
        if not ok:
            return jsonify({"error": storage_err}), 500
        fd, tmp_path = tempfile.mkstemp(suffix=".swu", dir=ota.inbox_dir)
        with os.fdopen(fd, "wb") as f:
            file.save(f)
    except OSError as e:
        return jsonify({"error": f"Upload failed: {e}"}), 500

    target = ota_service.extract_bundle_target(tmp_path)
    if target == "camera":
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify(
            {"error": "This is a camera bundle; upload a server bundle"}
        ), 400

    staged_path, err = ota.stage_bundle(
        tmp_path,
        filename,
        user=user,
        ip=ip,
        current_version=release_version(),
    )
    if err:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": err}), 400

    valid, verify_err = ota.verify_bundle(staged_path)
    if not valid:
        ota.clean_staging()
        return jsonify({"error": f"Verification failed: {verify_err}"}), 400

    staged_status = ota.get_status("server")
    return jsonify(
        {
            "message": "Update image staged and verified",
            "filename": filename,
            "staged_path": staged_path,
            "target_version": staged_status.get("target_version", ""),
            "verification": ota.get_verification_posture(),
        }
    ), 200


@ota_bp.route("/server/install", methods=["POST"])
@admin_required
@csrf_protect
def install_server_image():
    """Install a staged .swu bundle. Admin only."""
    ota = current_app.ota_service
    user = session.get("username", "")
    ip = request.remote_addr or ""

    # Same concurrent-install guard as upload. An install-in-progress
    # state would already block a second install below, but we surface
    # HTTP 409 up front so the UI doesn't have to parse a mid-install
    # error.
    state = ota.get_status("server").get("state", "idle")
    if state in ("installing", "verifying", "rebooting"):
        return jsonify(
            {"error": f"A server update is already in progress ({state})"}
        ), 409
    camera_lock = _reject_if_camera_update_active(ota)
    if camera_lock:
        return camera_lock

    staging = ota.staging_dir
    if not os.path.isdir(staging):
        return jsonify({"error": "No staged update found"}), 404

    # Pick the NEWEST .swu by mtime. A stale bundle from a previous
    # session (e.g. after an aborted install) would silently overwrite
    # the freshly uploaded one if we used the alphabetically first
    # entry, because sorted-by-filename happens to tie-break on
    # version strings whose lexicographic order doesn't match the
    # upload order.
    candidates = [
        (os.path.getmtime(os.path.join(staging, f)), f)
        for f in os.listdir(staging)
        if f.endswith(".swu")
    ]
    if not candidates:
        return jsonify({"error": "No staged update found"}), 404
    candidates.sort(reverse=True)
    bundle_path = os.path.join(staging, candidates[0][1])
    target_version = ota_service.extract_bundle_version(bundle_path)
    decision = ota_policy.classify_update(release_version(), target_version)
    if decision.blocked:
        try:
            os.unlink(bundle_path)
        except OSError:
            pass
        ota.set_status("server", "idle", progress=0, error=decision.reason)
        return jsonify({"error": decision.reason}), 409

    operation_token, operation_error = ota.begin_operation(
        ota_service.SERVER_INSTALL_OPERATION, ["server"]
    )
    if operation_error:
        return jsonify({"error": operation_error}), 409
    try:
        camera_lock = _reject_if_camera_update_active(ota)
        if camera_lock:
            return camera_lock

        ok, err = ota.install_bundle(bundle_path, user=user, ip=ip)
        if not ok:
            return jsonify({"error": err}), 500
        ota.clean_staging()

        # The button says "Install & Reboot", so actually reboot. We flush
        # the HTTP response first (the client needs the 200 to transition
        # its UI into the "rebooting" state) and schedule the reboot on a
        # background thread with a short delay so systemd has a moment to
        # tear down Flask cleanly.
        ota.prepare_reboot_activation(target_version)
        ota.set_status("server", "rebooting", progress=100, error="")
        ota.schedule_reboot()
        return jsonify(
            {"message": "Installation complete — rebooting now", "rebooting": True}
        ), 200
    finally:
        ota.release_operation(operation_token)


@ota_bp.route("/camera/<camera_id>/upload", methods=["POST"])
@admin_required
@csrf_protect
def upload_camera_image(camera_id):
    """Upload a .swu image for a specific camera (admin).

    Bundle is validated and stored once in /data/ota/camera-library.
    A subsequent POST to /camera/<id>/push streams it to the camera.
    Kept separate from install so an admin can stage a bundle in
    advance and trigger the push during a maintenance window.
    """
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    if not file.filename.lower().endswith(".swu"):
        return jsonify({"error": "Only .swu files are accepted"}), 400

    ota = current_app.ota_service

    # Refuse if a push for this camera is already in flight — would
    # either clobber the in-flight bundle or confuse the status UI.
    status = ota.get_status(camera_id)
    if status.get("state") in CAMERA_BUSY_STATES:
        return jsonify(
            {"error": f"Update already in progress ({status.get('state')})"}
        ), 409

    ok, storage_err = ota.ensure_storage()
    if not ok:
        return jsonify({"error": storage_err}), 500

    filename = ota_service.sanitize_bundle_filename(file.filename)
    if not filename:
        return jsonify({"error": "Only .swu files are accepted"}), 400

    library = _camera_library_dir(ota)
    fd, tmp_path = tempfile.mkstemp(suffix=".swu", dir=library)

    try:
        with os.fdopen(fd, "wb") as target_file:
            file.save(target_file)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": f"Upload failed: {exc}"}), 500

    try:
        size = os.path.getsize(tmp_path)
    except OSError:
        size = 0
    if size == 0:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": "Uploaded file is empty"}), 400

    target = ota_service.extract_bundle_target(tmp_path)
    if target == "server":
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify(
            {"error": "This is a server bundle; upload a camera bundle"}
        ), 400

    target_version = ota_service.extract_bundle_version(tmp_path)
    decision = ota_policy.classify_update(camera.firmware_version, target_version)
    if decision.blocked:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": decision.reason}), 400

    sha256 = _hash_file(tmp_path)
    stored_filename = f"{sha256[:12]}-{filename}"
    target_path = os.path.join(library, stored_filename)
    try:
        os.replace(tmp_path, target_path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": f"Upload failed: {exc}"}), 500
    record = _write_camera_bundle_record(ota, target_path, filename, sha256)

    ota.set_status(
        camera_id,
        "staged",
        version="",
        progress=0,
        error="",
        filename=record.get("original_filename") or record["filename"],
        staged_filename=record.get("original_filename") or record["filename"],
        target_version=target_version,
        update_relation=decision.relation,
    )
    audit = getattr(current_app, "audit", None)
    if audit:
        try:
            audit.log_event(
                "OTA_CAMERA_UPLOAD",
                user=session.get("username", ""),
                ip=request.remote_addr or "",
                detail=(
                    f"Uploaded {filename} for camera {camera_id} "
                    f"(version={target_version or 'unknown'})"
                ),
            )
        except Exception:
            pass

    return jsonify(
        {
            "message": "Bundle staged for camera",
            "camera_id": camera_id,
            "filename": record.get("original_filename") or record["filename"],
            "original_filename": filename,
            "target_version": target_version,
            "size": size,
        }
    ), 200


@ota_bp.route("/camera-library/upload", methods=["POST"])
@admin_required
@csrf_protect
def upload_camera_library_image():
    """Upload a reusable camera .swu bundle for fleet updates (admin)."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    filename = ota_service.sanitize_bundle_filename(file.filename)
    if not filename:
        return jsonify({"error": "Only .swu files are accepted"}), 400

    ota = current_app.ota_service
    ok, storage_err = ota.ensure_storage()
    if not ok:
        return jsonify({"error": storage_err}), 500

    record, size, err = _store_camera_library_upload(ota, file, filename)
    if err:
        status = 400 if "bundle" in err.lower() or "empty" in err.lower() else 500
        return jsonify({"error": err}), status

    cameras = current_app.store.get_cameras()
    audit = getattr(current_app, "audit", None)
    if audit:
        try:
            audit.log_event(
                "OTA_CAMERA_LIBRARY_UPLOAD",
                user=session.get("username", ""),
                ip=request.remote_addr or "",
                detail=(
                    f"Uploaded reusable camera bundle {filename} "
                    f"(version={record.get('target_version') or 'unknown'})"
                ),
            )
        except Exception:
            pass

    return jsonify(
        {
            "message": "Camera bundle saved",
            "filename": record.get("original_filename") or record["filename"],
            "original_filename": filename,
            "target_version": record.get("target_version") or "",
            "size": size,
            "camera_bundle": _camera_bundle_summary(record, cameras),
        }
    ), 200


@ota_bp.route("/camera/<camera_id>/custom-upload", methods=["POST"])
@admin_required
@csrf_protect
def upload_camera_custom_image(camera_id):
    """Upload a one-shot camera bundle for a single camera (admin)."""
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    filename = ota_service.sanitize_bundle_filename(file.filename)
    if not filename:
        return jsonify({"error": "Only .swu files are accepted"}), 400

    ota = current_app.ota_service
    status = ota.get_status(camera_id)
    if status.get("state") in CAMERA_BUSY_STATES:
        return jsonify(
            {"error": f"Update already in progress ({status.get('state')})"}
        ), 409

    ok, storage_err = ota.ensure_storage()
    if not ok:
        return jsonify({"error": storage_err}), 500

    camera_dir = _camera_custom_dir(ota, camera_id)
    _discard_camera_custom_record(ota, camera_id)
    try:
        os.makedirs(camera_dir, exist_ok=True)
    except OSError as exc:
        return jsonify({"error": f"Upload failed: {exc}"}), 500

    fd, tmp_path = tempfile.mkstemp(suffix=".swu", dir=camera_dir)
    try:
        with os.fdopen(fd, "wb") as target_file:
            file.save(target_file)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": f"Upload failed: {exc}"}), 500

    try:
        size = os.path.getsize(tmp_path)
    except OSError:
        size = 0
    if size == 0:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": "Uploaded file is empty"}), 400

    target = ota_service.extract_bundle_target(tmp_path)
    if target != "camera":
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if target == "server":
            return jsonify(
                {"error": "This is a server bundle; upload a camera bundle"}
            ), 400
        return jsonify(
            {"error": "This bundle target is not recognized as a camera bundle"}
        ), 400

    target_version = ota_service.extract_bundle_version(tmp_path)
    decision = ota_policy.classify_update(camera.firmware_version, target_version)
    if decision.blocked or decision.relation == "same":
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        reason = decision.reason or "Camera is already running this version"
        return jsonify({"error": reason}), 400

    sha256 = _hash_file(tmp_path)
    stored_filename = f"{sha256[:12]}-{filename}"
    target_path = os.path.join(camera_dir, stored_filename)
    try:
        os.replace(tmp_path, target_path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": f"Upload failed: {exc}"}), 500
    record = _write_camera_custom_record(ota, camera_id, target_path, filename, sha256)

    ota.set_status(
        camera_id,
        "staged",
        version="",
        progress=0,
        error="",
        filename=record.get("original_filename") or record["filename"],
        staged_filename=record.get("original_filename") or record["filename"],
        target_version=target_version,
        update_relation=decision.relation,
        bundle_scope="custom",
    )
    audit = getattr(current_app, "audit", None)
    if audit:
        try:
            audit.log_event(
                "OTA_CAMERA_CUSTOM_UPLOAD",
                user=session.get("username", ""),
                ip=request.remote_addr or "",
                detail=(
                    f"Uploaded one-shot bundle {filename} for camera {camera_id} "
                    f"(version={target_version or 'unknown'})"
                ),
            )
        except Exception:
            pass

    return jsonify(
        {
            "message": "Custom bundle staged for camera",
            "camera_id": camera_id,
            "filename": record.get("original_filename") or record["filename"],
            "original_filename": filename,
            "target_version": target_version,
            "size": size,
            "bundle_scope": "custom",
        }
    ), 200


@ota_bp.route("/camera/<camera_id>/custom-discard", methods=["POST"])
@admin_required
@csrf_protect
def discard_camera_custom_image(camera_id):
    """Discard a one-shot camera bundle and fall back to the common bundle."""
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    ota = current_app.ota_service
    status = ota.get_status(camera_id)
    if status.get("state") in CAMERA_BUSY_STATES:
        return jsonify(
            {"error": f"Update already in progress ({status.get('state')})"}
        ), 409

    _discard_camera_custom_record(ota, camera_id)
    ota.set_status(
        camera_id,
        "idle",
        progress=0,
        error="",
        staged_filename="",
        target_version="",
        update_relation="",
        bundle_scope="",
    )
    return jsonify({"message": "Custom bundle discarded", "camera_id": camera_id}), 200


def _run_camera_push(
    app,
    camera_id,
    camera_ip,
    bundle_path,
    filename,
    target_version,
    user,
    ip,
    bundle_scope="common",
    operation_token="",
):
    """Background job: stream the staged bundle to the camera.

    Runs inside an app context so we can keep touching current_app's
    services (ota_service, audit) from the worker thread.
    """
    with app.app_context():
        ota = app.ota_service
        client = app.camera_ota_client
        audit = getattr(app, "audit", None)

        def _progress(sent, total):
            # Map bytes-sent → 0..50 %. Used only for the byte-level
            # track within the "uploading" phase; high-level state
            # transitions are driven by _status below.
            if total > 0:
                pct = int((sent / total) * 50)
            else:
                pct = 0
            ota.set_status(
                camera_id,
                "uploading",
                progress=pct,
                error="",
                bytes_sent=sent,
                staged_filename=filename,
                target_version=target_version,
                bundle_scope=bundle_scope,
            )

        def _status(state, progress, error=""):
            # push_bundle's high-level state updates (installing,
            # rebooting, installed, error). Overwrites whatever
            # _progress last wrote so the UI reflects the real phase.
            kwargs = {"progress": progress, "error": error or ""}
            kwargs["staged_filename"] = filename
            kwargs["target_version"] = target_version
            kwargs["bundle_scope"] = bundle_scope
            ota.set_status(camera_id, state, **kwargs)

        ota.set_status(
            camera_id,
            "uploading",
            progress=0,
            error="",
            staged_filename=filename,
            target_version=target_version,
            bundle_scope=bundle_scope,
        )
        try:
            ok, msg = client.push_bundle(
                camera_ip,
                bundle_path,
                progress_cb=_progress,
                status_cb=_status,
                expected_version=target_version,
            )
        except Exception as exc:  # defensive — never leak out of the thread
            ok, msg = False, f"Unexpected error: {exc}"

        if ok:
            if bundle_scope == "custom":
                _discard_camera_custom_record(ota, camera_id)
            ota.set_status(
                camera_id,
                "installed",
                progress=100,
                error="",
                staged_filename=filename,
                target_version=target_version,
                bundle_scope=bundle_scope,
            )
            app.logger.info("OTA camera %s installed: %s", camera_id, msg)
            if audit:
                try:
                    audit.log_event(
                        "OTA_CAMERA_INSTALL_COMPLETE",
                        user=user,
                        ip=ip,
                        detail=f"Camera {camera_id} install: {msg}",
                    )
                except Exception:
                    pass
        else:
            ota.set_status(camera_id, "error", error=msg)
            app.logger.warning("OTA camera %s failed: %s", camera_id, msg)
            if audit:
                try:
                    audit.log_event(
                        "OTA_CAMERA_INSTALL_FAILED",
                        user=user,
                        ip=ip,
                        detail=f"Camera {camera_id} push failed: {msg}",
                    )
                except Exception:
                    pass
        if operation_token:
            ota.finish_operation_device(operation_token, camera_id)


@ota_bp.route("/camera/<camera_id>/push", methods=["POST"])
@admin_required
@csrf_protect
def push_camera_update(camera_id):
    """Stream the staged bundle to the camera and install it (admin).

    Dual-transport: the bundle lives on the server; this endpoint
    pushes it to the camera's OTAAgent via mTLS (ADR-0020). Returns
    202 immediately with a tracking id — the GUI polls /ota/status
    to render progress.
    """
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    if camera.status != "online":
        return jsonify({"error": "Camera must be online to receive updates"}), 400

    if not getattr(camera, "ip", ""):
        return jsonify({"error": "Camera IP not known — re-pair the camera"}), 400

    ota = current_app.ota_service
    ota.ensure_storage()
    server_lock = _reject_if_server_install_active(ota)
    if server_lock:
        return server_lock
    body = request.get_json(silent=True) or {}
    requested_scope = str(body.get("scope") or "common")
    if requested_scope == "custom":
        record = _read_camera_custom_record(ota, camera_id)
        if record is None:
            return jsonify({"error": "No custom bundle uploaded for this camera"}), 409
        decision = ota_policy.classify_update(
            camera.firmware_version, str(record.get("target_version") or "")
        )
    else:
        requested_scope = "common"
        record, decision = _best_camera_bundle_for(
            camera,
            _read_camera_bundle_records(ota),
        )
    if record is None:
        return jsonify(
            {"error": "No bundle uploaded for this camera — upload a .swu first"}
        ), 409

    bundle_path = record["path"]
    filename = record.get("original_filename") or record["filename"]
    target_version = str(record.get("target_version") or "")
    if decision and (decision.blocked or decision.relation == "same"):
        reason = decision.reason or "Camera is already running this version"
        ota.set_status(camera_id, "idle", progress=0, error=reason)
        if requested_scope == "custom":
            _discard_camera_custom_record(ota, camera_id)
        return jsonify({"error": reason}), 409

    status = ota.get_status(camera_id)
    if status.get("state") in CAMERA_BUSY_STATES:
        return jsonify(
            {"error": f"Update already in progress ({status.get('state')})"}
        ), 409

    operation_token, operation_error = ota.begin_operation(
        ota_service.CAMERA_UPDATE_OPERATION, [camera_id]
    )
    if operation_error:
        return jsonify({"error": operation_error}), 409
    server_lock = _reject_if_server_install_active(ota)
    if server_lock:
        ota.release_operation(operation_token)
        return server_lock

    user = session.get("username", "")
    ip = request.remote_addr or ""

    audit = getattr(current_app, "audit", None)
    if audit:
        try:
            audit.log_event(
                "OTA_CAMERA_PUSH",
                user=user,
                ip=ip,
                detail=(
                    f"Pushing {filename} to camera {camera_id} ({requested_scope})"
                ),
            )
        except Exception:
            pass

    ota.set_status(
        camera_id,
        "uploading",
        progress=0,
        error="",
        staged_filename=filename,
        target_version=target_version,
        update_relation=decision.relation if decision else "unknown",
        bundle_scope=requested_scope,
    )

    # Kick off push in a background thread so the HTTP request
    # returns immediately. A 150 MB bundle over WiFi can take a
    # minute; blocking the Flask worker would starve the rest of
    # the UI and bump into gunicorn's worker timeout.
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_camera_push,
        args=(
            app,
            camera_id,
            camera.ip,
            bundle_path,
            filename,
            target_version,
            user,
            ip,
            requested_scope,
            operation_token,
        ),
        name=f"ota-push-{camera_id}",
        daemon=True,
    )
    thread.start()

    return jsonify(
        {
            "message": "Update push started",
            "camera_id": camera_id,
            "filename": filename,
            "target_version": target_version,
        }
    ), 202


@ota_bp.route("/cameras/push", methods=["POST"])
@admin_required
@csrf_protect
def push_all_eligible_cameras():
    """Push the reusable camera bundle to all online eligible cameras."""
    ota = current_app.ota_service
    ota.ensure_storage()
    server_lock = _reject_if_server_install_active(ota)
    if server_lock:
        return server_lock
    record = _latest_camera_bundle(_read_camera_bundle_records(ota))
    if record is None:
        return jsonify({"error": "No reusable camera bundle uploaded"}), 409

    bundle_path = record["path"]
    filename = record.get("original_filename") or record["filename"]
    target_version = str(record.get("target_version") or "")
    cameras = current_app.store.get_cameras()
    user = session.get("username", "")
    ip = request.remote_addr or ""
    app = current_app._get_current_object()
    operation_token, operation_error = ota.begin_operation(
        ota_service.CAMERA_UPDATE_OPERATION, []
    )
    if operation_error:
        return jsonify({"error": operation_error}), 409

    started = []
    skipped = []
    for camera in cameras:
        if camera.status == "pending":
            continue
        decision = ota_policy.classify_update(camera.firmware_version, target_version)
        reason = ""
        if decision.blocked:
            reason = decision.reason or "blocked"
        elif decision.relation == "same":
            reason = "already current"
        elif camera.status != "online":
            reason = "offline"
        elif not getattr(camera, "ip", ""):
            reason = "IP not known"
        else:
            status = ota.get_status(camera.id)
            if status.get("state") in CAMERA_BUSY_STATES:
                reason = f"busy ({status.get('state')})"

        if reason:
            skipped.append({"camera_id": camera.id, "reason": reason})
            continue

        ota.set_status(
            camera.id,
            "uploading",
            progress=0,
            error="",
            staged_filename=filename,
            target_version=target_version,
            update_relation=decision.relation,
            bundle_scope="common",
        )
        thread = threading.Thread(
            target=_run_camera_push,
            args=(
                app,
                camera.id,
                camera.ip,
                bundle_path,
                filename,
                target_version,
                user,
                ip,
                "common",
                operation_token,
            ),
            name=f"ota-push-{camera.id}",
            daemon=True,
        )
        started.append(camera.id)
        ota.update_operation_devices(operation_token, started)
        thread.start()

    if started:
        ota.update_operation_devices(operation_token, started)
    else:
        ota.release_operation(operation_token)

    audit = getattr(current_app, "audit", None)
    if audit and started:
        try:
            audit.log_event(
                "OTA_CAMERA_PUSH",
                user=user,
                ip=ip,
                detail=(
                    f"Pushing {filename} to {len(started)} camera(s); "
                    f"skipped {len(skipped)}"
                ),
            )
        except Exception:
            pass

    if not started:
        return jsonify(
            {
                "error": "No eligible online cameras for this bundle",
                "skipped": skipped,
            }
        ), 409

    return jsonify(
        {
            "message": f"Update started for {len(started)} camera(s)",
            "camera_ids": started,
            "skipped": skipped,
            "filename": filename,
            "target_version": target_version,
        }
    ), 202


@ota_bp.route("/camera/<camera_id>/live-status", methods=["GET"])
@login_required
def live_camera_status(camera_id):
    """Fetch the camera's own OTA agent status in real time.

    The server tracks a "shadow" status via ota_service for fast UI
    polling, but during the install phase (after upload completes)
    only the camera knows the true state. This endpoint proxies the
    camera's /ota/status so the UI can show the verifying/installing
    phases accurately.
    """
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404
    if not getattr(camera, "ip", ""):
        return jsonify({"error": "Camera IP not known"}), 400

    client = current_app.camera_ota_client
    status, err = client.get_status(camera.ip)
    if err:
        return jsonify({"error": err, "reachable": False}), 200
    return jsonify({"reachable": True, **(status or {})}), 200


@ota_bp.route("/usb/scan", methods=["GET"])
@admin_required
def scan_usb():
    """Scan USB devices for .swu update bundles. Admin only."""
    ota = current_app.ota_service
    bundles = ota.scan_usb()
    return jsonify({"bundles": bundles}), 200


@ota_bp.route("/usb/import", methods=["POST"])
@admin_required
@csrf_protect
def import_from_usb():
    """Import a .swu bundle from a USB device. Admin only.

    Request body: {"path": "/mnt/recordings/updates/update-1.2.swu"}
    """
    ota = current_app.ota_service
    data = request.get_json(silent=True) or {}
    usb_path = data.get("path", "")

    if not usb_path:
        return jsonify({"error": "No file path provided"}), 400

    user = session.get("username", "")
    ip = request.remote_addr or ""

    staged_path, err = ota.import_from_usb(usb_path, user=user, ip=ip)
    if err:
        return jsonify({"error": err}), 400

    valid, verify_err = ota.verify_bundle(staged_path)
    if not valid:
        ota.clean_staging()
        return jsonify({"error": f"Verification failed: {verify_err}"}), 400

    return jsonify(
        {
            "message": "USB bundle imported, staged, and verified",
            "staged_path": staged_path,
        }
    ), 200

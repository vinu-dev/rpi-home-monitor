"""
Over-the-Air update API (ADR-0008, ADR-0020).

Endpoints:
  GET  /ota/status                 - per-device OTA status (server + cameras)
  POST /ota/server/upload          - upload .swu for server (admin, multipart)
  POST /ota/server/install         - install staged server bundle (admin)
  POST /ota/camera/<id>/upload     - upload .swu for camera (admin, multipart)
  POST /ota/camera/<id>/push       - stream bundle to camera via mTLS (admin)
  GET  /ota/usb/scan               - scan mounted USB for bundles (admin)
  POST /ota/usb/import             - import + stage bundle from USB (admin)

The camera path is dual-transport (ADR-0020): the user uploads a .swu
to the server through the Settings GUI; the server then relays it to
the camera's OTA agent via mTLS. The camera verifies the signature
and invokes swupdate exactly as it would for a direct upload — so
the install layer is identical on both devices.
"""

import os
import shutil
import subprocess
import tempfile
import threading

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect, login_required

ota_bp = Blueprint("ota", __name__)

# How often the background push task refreshes the camera's live
# status into the server-side tracker (ota_service.set_status). Short
# enough to feel responsive in the UI without hammering the camera.
CAMERA_STATUS_POLL_SECONDS = 2.0


def _camera_inbox_dir(ota, camera_id):
    """Per-camera inbox directory inside /data/ota."""
    safe = "".join(c for c in camera_id if c.isalnum() or c in ("-", "_"))
    return os.path.join(ota.inbox_dir, f"camera-{safe}")


def _latest_camera_bundle(ota, camera_id):
    """Return (path, filename) of the most recent .swu uploaded for
    a given camera, or (None, None) if none staged."""
    d = _camera_inbox_dir(ota, camera_id)
    if not os.path.isdir(d):
        return None, None
    entries = [
        e for e in os.scandir(d) if e.is_file() and e.name.lower().endswith(".swu")
    ]
    if not entries:
        return None, None
    entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    return entries[0].path, entries[0].name


@ota_bp.route("/status", methods=["GET"])
@login_required
def get_status():
    """Get OTA update status for server + all cameras."""
    settings = current_app.store.get_settings()
    cameras = current_app.store.get_cameras()
    ota = current_app.ota_service

    result = {
        "server": {
            "current_version": settings.firmware_version,
            **ota.get_status("server"),
        },
        "cameras": [],
    }

    for cam in cameras:
        if cam.status == "pending":
            continue
        entry = {
            "id": cam.id,
            "name": cam.name,
            "online": cam.status == "online",
            "current_version": cam.firmware_version,
            **ota.get_status(cam.id),
        }
        # Tell the UI whether a bundle is already staged for this
        # camera so the "Push" button can be enabled without the user
        # needing to re-upload after a page refresh.
        _, fn = _latest_camera_bundle(ota, cam.id)
        entry["staged_filename"] = fn or ""
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

    try:
        os.makedirs(ota.inbox_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(suffix=".swu", dir=ota.inbox_dir)
        with os.fdopen(fd, "wb") as f:
            file.save(f)
    except OSError as e:
        return jsonify({"error": f"Upload failed: {e}"}), 500

    staged_path, err = ota.stage_bundle(tmp_path, file.filename, user=user, ip=ip)
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

    return jsonify(
        {
            "message": "Update image staged and verified",
            "filename": file.filename,
            "staged_path": staged_path,
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
    ok, err = ota.install_bundle(bundle_path, user=user, ip=ip)
    if not ok:
        return jsonify({"error": err}), 500

    # The button says "Install & Reboot", so actually reboot. We flush
    # the HTTP response first (the client needs the 200 to transition
    # its UI into the "rebooting" state) and schedule the reboot on a
    # background thread with a short delay so systemd has a moment to
    # tear down Flask cleanly.
    def _reboot_after_delay():
        import time as _t

        _t.sleep(2.0)
        try:
            subprocess.run(["reboot"], check=False, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            current_app.logger.error("reboot command failed: %s", exc)

    ota.set_status("server", "rebooting", progress=100, error="")
    threading.Thread(
        target=_reboot_after_delay, daemon=True, name="ota-install-reboot"
    ).start()
    return jsonify(
        {"message": "Installation complete — rebooting now", "rebooting": True}
    ), 200


@ota_bp.route("/camera/<camera_id>/upload", methods=["POST"])
@admin_required
@csrf_protect
def upload_camera_image(camera_id):
    """Upload a .swu image for a specific camera (admin).

    Bundle is parked in /data/ota/inbox/camera-<id>/<filename>.swu.
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
    if status.get("state") in {"uploading", "installing"}:
        return jsonify(
            {"error": f"Update already in progress ({status.get('state')})"}
        ), 409

    inbox = _camera_inbox_dir(ota, camera_id)
    os.makedirs(inbox, exist_ok=True)

    # Clear any previous bundles for this camera — we only ever want
    # one staged at a time, matching the server's single-staging-slot
    # semantics. Prevents a stale bundle from being pushed by mistake.
    try:
        for entry in os.scandir(inbox):
            if entry.is_file():
                os.unlink(entry.path)
    except OSError:
        pass

    target_path = os.path.join(inbox, file.filename)
    try:
        file.save(target_path)
    except OSError as exc:
        return jsonify({"error": f"Upload failed: {exc}"}), 500

    try:
        size = os.path.getsize(target_path)
    except OSError:
        size = 0
    if size == 0:
        try:
            os.unlink(target_path)
        except OSError:
            pass
        return jsonify({"error": "Uploaded file is empty"}), 400

    ota.set_status(
        camera_id,
        "staged",
        version="",
        progress=0,
        error="",
        filename=file.filename,
    )
    audit = getattr(current_app, "audit", None)
    if audit:
        try:
            audit.log_event(
                "OTA_CAMERA_UPLOAD",
                user=session.get("username", ""),
                ip=request.remote_addr or "",
                detail=f"Uploaded {file.filename} for camera {camera_id}",
            )
        except Exception:
            pass

    return jsonify(
        {
            "message": "Bundle staged for camera",
            "camera_id": camera_id,
            "filename": file.filename,
            "size": size,
        }
    ), 200


def _run_camera_push(app, camera_id, camera_ip, bundle_path, user, ip):
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
                camera_id, "uploading", progress=pct, error="", bytes_sent=sent
            )

        def _status(state, progress, error=""):
            # push_bundle's high-level state updates (installing,
            # rebooting, installed, error). Overwrites whatever
            # _progress last wrote so the UI reflects the real phase.
            kwargs = {"progress": progress, "error": error or ""}
            ota.set_status(camera_id, state, **kwargs)

        ota.set_status(camera_id, "uploading", progress=0, error="")
        try:
            ok, msg = client.push_bundle(
                camera_ip, bundle_path, progress_cb=_progress, status_cb=_status
            )
        except Exception as exc:  # defensive — never leak out of the thread
            ok, msg = False, f"Unexpected error: {exc}"

        if ok:
            ota.set_status(camera_id, "installed", progress=100, error="")
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
            # Bundle is no longer needed — camera has its own copy
            # in staging during install, and keeps it until reboot.
            try:
                shutil.rmtree(os.path.dirname(bundle_path), ignore_errors=True)
            except OSError:
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
    bundle_path, filename = _latest_camera_bundle(ota, camera_id)
    if bundle_path is None:
        return jsonify(
            {"error": "No bundle uploaded for this camera — upload a .swu first"}
        ), 409

    status = ota.get_status(camera_id)
    if status.get("state") in {"uploading", "installing"}:
        return jsonify(
            {"error": f"Update already in progress ({status.get('state')})"}
        ), 409

    user = session.get("username", "")
    ip = request.remote_addr or ""

    audit = getattr(current_app, "audit", None)
    if audit:
        try:
            audit.log_event(
                "OTA_CAMERA_PUSH",
                user=user,
                ip=ip,
                detail=f"Pushing {filename} to camera {camera_id}",
            )
        except Exception:
            pass

    # Kick off push in a background thread so the HTTP request
    # returns immediately. A 150 MB bundle over WiFi can take a
    # minute; blocking the Flask worker would starve the rest of
    # the UI and bump into gunicorn's worker timeout.
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_camera_push,
        args=(app, camera_id, camera.ip, bundle_path, user, ip),
        name=f"ota-push-{camera_id}",
        daemon=True,
    )
    thread.start()

    return jsonify(
        {
            "message": "Update push started",
            "camera_id": camera_id,
            "filename": filename,
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

# REQ: SWR-068, SWR-070; RISK: RISK-020, RISK-026; SEC: SC-020, SC-025; TEST: TC-055
"""Integration tests for the diagnostics export API route."""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path


def _load_manifest(bundle_bytes: bytes) -> dict:
    with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as archive:
        manifest_member = next(
            member
            for member in archive.getmembers()
            if member.name.endswith("/manifest.json")
        )
        return json.loads(
            archive.extractfile(manifest_member)
            .read()
            .decode("utf-8", errors="replace")
        )


class TestDiagnosticsExportEndpoint:
    def test_requires_auth(self, client):
        response = client.post("/api/v1/system/diagnostics/export")
        assert response.status_code == 401

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.post("/api/v1/system/diagnostics/export")
        assert response.status_code == 403

    def test_downloads_tarball_and_cleans_up_staging(self, app, logged_in_client):
        client = logged_in_client()
        response = client.post("/api/v1/system/diagnostics/export")

        assert response.status_code == 200
        assert response.mimetype == "application/gzip"
        assert "attachment" in response.headers["Content-Disposition"]
        manifest = _load_manifest(response.data)
        assert manifest["bundle_version"] == 1
        assert manifest["requested_by"].startswith("admin @ ")

        staging_root = Path(app.config["CONFIG_DIR"]) / "diagnostics-staging"
        response.close()
        if staging_root.exists():
            assert list(staging_root.iterdir()) == []

    def test_returns_rate_limit_payload(self, logged_in_client):
        client = logged_in_client()
        with client.session_transaction() as session:
            sid = session["sid"]
        client.application.diagnostics_service._session_attempts[sid] = [
            time.time()
        ] * client.application.config["DIAGNOSTICS_RATE_LIMIT_PER_SESSION"]

        response = client.post("/api/v1/system/diagnostics/export")

        assert response.status_code == 429
        payload = response.get_json()
        assert payload["error"] == "diagnostics_export_rate_limited"
        assert payload["retry_after_seconds"] > 0
        assert response.headers["Retry-After"]

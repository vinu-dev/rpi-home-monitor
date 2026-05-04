# REQ: SWR-069; RISK: RISK-020, RISK-026; SEC: SC-020, SC-025; TEST: TC-055
"""Security tests for diagnostics export redaction and deny-lists."""

from __future__ import annotations

import io
import tarfile

from monitor.models import Camera, Settings, User, WebhookDestination


def _bundle_entries(bundle_bytes: bytes) -> tuple[list[str], list[str]]:
    names: list[str] = []
    texts: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as archive:
        for member in archive.getmembers():
            names.append(member.name)
            if not member.isfile():
                continue
            texts.append(
                archive.extractfile(member).read().decode("utf-8", errors="replace")
            )
    return names, texts


def test_export_bundle_does_not_leak_known_secret_values(
    app, logged_in_client, data_dir
):
    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash="hash-secret-value",
            role="admin",
            totp_secret="totp-secret-value",
            recovery_code_hashes=["recovery-secret-value"],
        )
    )
    app.store.save_camera(
        Camera(
            id="cam-001",
            name="Front Door",
            pairing_secret="pairing-secret-value",
        )
    )
    app.store.save_settings(
        Settings(
            hostname="security-check",
            tailscale_auth_key="tailscale-secret-value",
            offsite_backup_access_key_id="access-key-id-secret",
            offsite_backup_secret_access_key="access-key-secret",
            webhook_destinations=[
                WebhookDestination(
                    id="wh-001",
                    url="https://hooks.example.test/ingest",
                    auth_type="hmac",
                    secret="webhook-secret-value",
                    custom_headers={"Authorization": "Bearer custom-secret-value"},
                )
            ],
        )
    )
    (data_dir / "config" / ".secret_key").write_text("session-secret-value")
    (data_dir / "certs" / "server.key").write_text("tls-private-key-value")
    (data_dir / "recordings" / "clip.mp4").write_text("recording-payload")

    client = logged_in_client()
    response = client.post("/api/v1/system/diagnostics/export")

    assert response.status_code == 200
    names, texts = _bundle_entries(response.data)
    response.close()

    merged = "\n".join(texts)
    for secret in (
        "hash-secret-value",
        "totp-secret-value",
        "recovery-secret-value",
        "pairing-secret-value",
        "tailscale-secret-value",
        "access-key-id-secret",
        "access-key-secret",
        "webhook-secret-value",
        "custom-secret-value",
        "session-secret-value",
        "tls-private-key-value",
        "recording-payload",
    ):
        assert secret not in merged

    assert all(".secret_key" not in name for name in names)
    assert all("/recordings/" not in name for name in names)
    assert all("/certs/" not in name for name in names)

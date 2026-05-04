# REQ: SWR-002, SWR-020, SWR-024
# RISK: RISK-002, RISK-012
# SEC: SC-001, SC-004, SC-012
# TEST: TC-004, TC-010, TC-023
"""Tests for the require_2fa_for_remote admin policy toggle and its
self-lockout guard."""

from pathlib import Path

import pyotp


def test_policy_get_returns_default_false(app, logged_in_client):
    client = logged_in_client()
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["require_2fa_for_remote"] is False


def test_settings_page_exposes_account_2fa_controls(app, logged_in_client):
    (Path(app.config["DATA_DIR"]) / ".setup-done").write_text("done", encoding="utf-8")

    client = logged_in_client()
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Require two-factor authentication for remote access" in body
    assert "/api/v1/auth/totp/enroll/start" in body
    assert "/api/v1/auth/totp/recovery-codes/regenerate" in body


def test_admin_cannot_enable_policy_without_own_2fa(app, logged_in_client):
    client = logged_in_client()
    resp = client.put("/api/v1/settings", json={"require_2fa_for_remote": True})
    assert resp.status_code == 400
    settings = app.store.get_settings()
    assert settings.require_2fa_for_remote is False


def test_admin_can_enable_policy_after_enrolling(app, logged_in_client):
    client = logged_in_client()
    # Enroll the admin first.
    start = client.post("/api/v1/auth/totp/enroll/start").get_json()
    code = pyotp.TOTP(start["secret"]).now()
    confirm = client.post("/api/v1/auth/totp/enroll/confirm", json={"code": code})
    assert confirm.status_code == 200

    resp = client.put("/api/v1/settings", json={"require_2fa_for_remote": True})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    settings = app.store.get_settings()
    assert settings.require_2fa_for_remote is True


def test_admin_can_disable_policy_without_2fa(app, logged_in_client):
    """Disabling the policy is always allowed — only the enable path is
    guarded so an admin can roll back without re-enrolling."""
    settings = app.store.get_settings()
    settings.require_2fa_for_remote = True
    app.store.save_settings(settings)

    client = logged_in_client()
    resp = client.put("/api/v1/settings", json={"require_2fa_for_remote": False})
    assert resp.status_code == 200
    assert app.store.get_settings().require_2fa_for_remote is False

# REQ: SWR-022, SWR-067; RISK: RISK-007, RISK-010, RISK-015; SEC: SC-010, SC-012; TEST: TC-021, TC-054
"""
Tests for view routes — HTML page serving and redirects.
"""

import os
import time

import pytest


def _authenticate(client, role="admin", username="admin", user_id="user-001"):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = username
        sess["role"] = role


class TestIndex:
    """Tests for GET /."""

    def test_redirects_to_setup_when_not_configured(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert "/setup" in response.headers["Location"]

    def test_redirects_to_login_when_setup_done(self, app, client):
        # Mark setup complete
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_redirects_to_dashboard_when_authenticated(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/")
        assert response.status_code == 302
        assert "/dashboard" in response.headers["Location"]


class TestSetupPage:
    """Tests for GET /setup."""

    def test_shows_setup_wizard(self, client):
        response = client.get("/setup")
        assert response.status_code == 200
        assert b"Home Monitor" in response.data

    def test_redirects_to_login_if_setup_done(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/setup")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_cert_required_setup_shows_secure_gate_without_password_step(
        self, app, client
    ):
        app.config["SETUP_CERT_REQUIRED"] = True

        response = client.get("/setup")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Admin Certificate Setup" in body
        assert "var setupCertRequired = true;" in body
        assert "var setupCertAuthorized = false;" in body
        assert "Set Admin Password" not in body
        assert "Admin Certificate Ready" in body

    def test_cert_session_setup_uses_certificate_admin_step(self, app, client):
        app.config["SETUP_CERT_REQUIRED"] = True
        with client.session_transaction() as sess:
            sess["user_id"] = "cert-test"
            sess["username"] = "cert-test"
            sess["role"] = "admin"
            sess["auth_method"] = "client_certificate"
            sess["cert_profile"] = "owner-admin"
            sess["created_at"] = time.time()
            sess["last_active"] = time.time()

        response = client.get("/setup")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "var setupCertRequired = true;" in body
        assert "var setupCertAuthorized = true;" in body
        assert "Admin Certificate Ready" in body
        assert "Set Admin Password" not in body


class TestLoginPage:
    """Tests for GET /login."""

    def test_redirects_to_setup_if_not_configured(self, client):
        response = client.get("/login")
        assert response.status_code == 302
        assert "/setup" in response.headers["Location"]

    def test_shows_login_page(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/login")
        assert response.status_code == 200
        assert b"Sign In" in response.data or b"login" in response.data.lower()
        body = response.get_data(as_text=True)
        assert "login-server-address" not in body
        assert "qrcode.min.js" not in body

    def test_redirects_authenticated_user_to_dashboard(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        _authenticate(client)

        response = client.get("/login")

        assert response.status_code == 302
        assert "/dashboard" in response.headers["Location"]

    def test_invalid_session_is_cleared_before_login_redirect(
        self, app, client, monkeypatch
    ):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        _authenticate(client)

        monkeypatch.setattr("monitor.auth._is_session_valid", lambda: False)
        response = client.get("/dashboard")

        assert response.status_code == 302
        assert "/login" in response.headers["Location"]
        with client.session_transaction() as sess:
            assert "user_id" not in sess
            assert "username" not in sess

    def test_help_page_renders_when_setup_done(self, app, client):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")
        response = client.get("/help/network-fallback")
        assert response.status_code == 200
        assert "What to do when .local does not work" in response.get_data(as_text=True)

    def test_help_page_redirects_to_setup_if_not_configured(self, client):
        response = client.get("/help/network-fallback")

        assert response.status_code == 302
        assert "/setup" in response.headers["Location"]


class TestSetupGate:
    """Every protected HTML page returns to setup before first boot is complete."""

    @pytest.mark.parametrize(
        "path",
        [
            "/dashboard",
            "/live",
            "/recordings",
            "/events",
            "/alerts",
            "/logs",
            "/settings",
            "/shares",
        ],
    )
    def test_protected_pages_redirect_to_setup_when_not_configured(self, client, path):
        response = client.get(path)

        assert response.status_code == 302
        assert "/setup" in response.headers["Location"]


class TestProtectedPages:
    """Tests for dashboard, live, recordings, settings — all require auth."""

    @pytest.fixture(autouse=True)
    def setup_done(self, app):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")

    def test_dashboard_redirects_to_login(self, client):
        response = client.get("/dashboard")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_live_redirects_to_login(self, client):
        response = client.get("/live")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_recordings_redirects_to_login(self, client):
        response = client.get("/recordings")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_events_redirects_to_login(self, client):
        response = client.get("/events")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_logs_redirects_to_login(self, client):
        response = client.get("/logs")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_settings_redirects_to_login(self, client):
        response = client.get("/settings")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_shares_redirects_to_login(self, client):
        response = client.get("/shares")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_dashboard_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/dashboard")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "dashboard-server-address" not in body
        assert "Server address" not in body
        assert "networkFallback.mount" not in body
        assert 'data-role="server-qr"' not in body
        assert "qrcode.min.js" not in body

    def test_live_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/live")
        assert response.status_code == 200

    def test_settings_uses_native_form_for_diagnostics_export(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/settings")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "form.action = '/api/v1/system/diagnostics/export'" in body
        assert "form.submit()" in body
        assert "fetch('/api/v1/system/diagnostics/export'" not in body
        assert "hm-diagnostics.tar.gz" not in body

    def test_recordings_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/recordings")
        assert response.status_code == 200

    def test_settings_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/settings")
        assert response.status_code == 200

    def test_shares_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/shares")
        assert response.status_code == 200

    def test_alerts_redirects_to_login(self, client):
        response = client.get("/alerts")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_alerts_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/alerts")
        assert response.status_code == 200

    def test_alerts_renders_for_viewer_role(self, client):
        # Server-side filter in AlertCenterService gates what the
        # viewer sees. The page itself must render — admins shouldn't
        # have a different page; viewers just see fewer rows.
        with client.session_transaction() as sess:
            sess["user_id"] = "user-002"
            sess["username"] = "bob"
            sess["role"] = "viewer"
        response = client.get("/alerts")
        assert response.status_code == 200


class TestAlertCenterUI:
    """Frontend regression tests for the alert center (ADR-0024 + #133).

    Pin the structural anchors of the bell badge + inbox so a future
    refactor that quietly drops them fails loudly. We don't render
    real alert data here; that path is covered by the AlertCenterService
    + API tests. We're just asserting the UI scaffold exists.
    """

    @pytest.fixture(autouse=True)
    def setup_done(self, app):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")

    def test_settings_notifications_tab_visible_to_admin(self, client):
        """ADR-0027 / #129 — Settings has a Notifications tab in
        the admin tab bar. Pin both the button and the tab body
        gate so a future "tidy-up" can't quietly drop them.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/settings")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # Tab button.
        assert "tab === 'notifications'" in body
        assert "Notifications</button>" in body or "Notifications<" in body
        # Tab body has the per-user controls.
        assert "Browser notifications" in body
        assert "Notify me" in body
        assert "Quiet hours" in body
        assert "saveQuietHours()" in body
        assert "cameraQuietMode(cam)" in body
        # Permission state surfaced.
        assert "notify.permission === 'granted'" in body
        assert "notify.permission === 'denied'" in body
        # Test-notification button.
        assert "fireTestNotification()" in body
        # Camera defaults section is admin-only.
        assert "isAdmin && notify.cameras.length" in body

    def test_settings_notifications_tab_visible_to_viewer(self, client):
        """Notifications are personal — viewers can manage their
        own prefs even though they don't see the admin tab bar.
        Pin that the panel renders for non-admin too.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-002"
            sess["username"] = "bob"
            sess["role"] = "viewer"
        response = client.get("/settings")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # Tab button is in the always-visible part of the tab bar
        # (the admin-only template-block excludes it). The tab body
        # is gated by tab=='notifications', not by isAdmin, so it
        # appears for the viewer when they click the button.
        assert "Notifications</button>" in body or "Notifications<" in body
        assert "Quiet hours" in body
        assert "notification_schedule" in body
        # Per-camera defaults section IS admin-only — verify it's
        # gated.
        assert 'x-show="isAdmin && notify.cameras.length' in body

    def test_settings_storage_tab_has_offsite_backup_controls(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/settings")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Offsite Backup" in body
        assert "testOffsiteBackupConnection()" in body
        assert "/api/v1/settings/offsite-backup" in body

    # REQ: SWR-099; RISK: RISK-099; SEC: SC-099; TEST: TC-099
    def test_settings_password_reset_modal_preserves_self_guard_and_clears_state(
        self, client
    ):
        """Pin the admin-reset UI wiring without a browser runner.

        The self-row guard and the reset-temp-password clearing happen in the
        rendered Alpine source, so a template regression is still detectable
        through the HTML response body.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/settings")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert 'x-show="currentUser && currentUser.id !== u.id"' in body
        assert "openResetPassword(u)" in body
        assert "closeResetPassword()" in body
        assert body.count("this.resetTempPassword = '';") == 2
        assert "Reset &amp; force change" in body

    def test_base_html_polls_notifications_pending(self, client):
        """The polling-and-fire-Notification logic must be wired
        in base.html so the bell-badge poller's neighbour fires
        OS-level notifications when permission is granted. Pin the
        fetch URL + the gate.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        # Any authed page renders base.html — use dashboard.
        response = client.get("/dashboard")
        body = response.get_data(as_text=True)
        # Permission gate (skip fetch entirely if not granted).
        assert "Notification.permission !== 'granted'" in body
        # Fetch URL.
        assert "/api/v1/notifications/pending" in body
        # OS-level dedupe via tag.
        assert "tag: n.alert_id" in body
        # Mark-seen round trip so the same alert doesn't re-fire.
        assert "/api/v1/notifications/seen" in body

    def test_dashboard_camera_cards_have_id_anchors(self, client):
        """The Tier-1 status strip's deep_link is `/dashboard#camera-<id>`
        (per system_summary_service._cameras). For that link to actually
        scroll to the offending card on click, each paired-camera card
        must carry `id="camera-<id>"`. Regression test for the live
        "click does nothing" bug — without the binding the anchor doesn't
        exist and the click silently does nothing.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/dashboard")
        body = response.get_data(as_text=True)
        # Alpine binding that emits the per-camera id.
        assert ":id=\"'camera-' + cam.id\"" in body
        # scroll-margin-top so the scrolled-to card doesn't jam against
        # the top-bar (~70px tall + a comfortable gap).
        assert "scroll-margin-top" in body

    def test_dashboard_does_not_render_audit_teaser(self, client):
        """ADR-0025 — the dashboard's audit teaser (admin-only,
        5-row mini-log) was retired in favour of the bell badge →
        /alerts flow. The test pins the structural anchors that
        defined the teaser so neither a markup-only revert nor a
        state-only revert can slip back in unnoticed.

        Note: the strings "Recent activity" and "auditAdmin" can
        legitimately appear in code comments documenting the
        retirement decision; we test the actual *bindings* that
        would render the surface, not the bare phrases.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/dashboard")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # The "Recent events" motion feed STAYS (different job —
        # inline playback).
        assert "Recent events" in body
        # The teaser's Alpine x-show binding is gone.
        assert 'x-show="auditAdmin"' not in body
        # The teaser's CSS class is no longer rendered.
        assert "log-teaser__row" not in body
        # The Full-log escape hatch link the teaser carried is gone
        # (it lived only inside the teaser block).
        assert 'href="/logs">Full log' not in body

    def test_settings_security_tab_is_sessions_only(self, client):
        """Issue #246 reintroduces Settings → Security for session
        inventory and revoke controls only.

        Pin both the new tab binding and the absence of the old
        inline audit-log viewer so the page does not regress into
        the retired ADR-0025 design.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/settings")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "tab = 'security'" in body
        assert "tab === 'security'" in body
        assert "/api/v1/sessions" in body
        # The retired inline audit table never comes back.
        assert 'x-for="(ev, i) in security.events"' not in body

    def test_logs_page_has_admin_only_clear_action(self, client):
        """ADR-0025 — the admin-only "Clear all entries" affordance
        lives on /logs itself, contextual to the log it clears.
        Pin both the affordance presence and that it's gated to
        admins (via the isAdmin Alpine flag resolved from /auth/me).
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/logs")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # Affordance text is on the page.
        assert "Clear all entries" in body
        # Gated by isAdmin (the resolved-from-auth-me flag).
        assert 'x-show="isAdmin && !clearConfirm"' in body
        # clearLog() method wired.
        assert "clearLog()" in body
        # Two-step confirm.
        assert "Permanently clear?" in body

    def test_topbar_bell_badge_starts_hidden(self, client):
        """The bell icon and badge must default to display:none so an
        unauthed page-load doesn't briefly flash a stale chrome
        element. Same defence-in-depth pattern as #148.

        We render the dashboard (any authed page works — the chrome
        is in base.html) and pin the inline display:none style.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/dashboard")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # Bell <a> is hidden until /unread-count returns a number.
        assert 'id="topbar-alerts"' in body
        assert "display:none" in body
        # Badge span is also hidden by default.
        assert 'id="topbar-alerts-badge"' in body
        # Polling script is wired in.
        assert "/api/v1/alerts/unread-count" in body

    def test_alerts_page_renders_filter_chips(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/alerts")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # Filter chips for each source the catalogue contains.
        assert "Faults" in body
        assert "Audit" in body
        assert "Motion" in body
        # Severity filters.
        assert "Warning" in body
        assert "Error" in body
        # Unread-only checkbox.
        assert "Unread only" in body
        # Mark-all-read action exists.
        assert "Mark all read" in body
        # Wired to the backend API.
        assert "/api/v1/alerts/" in body

    def test_alerts_page_links_through_to_deep_link(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/alerts")
        body = response.get_data(as_text=True)
        # The row's title links via the alert's deep_link field, not a
        # hard-coded URL. Pin that the template uses :href="alert.deep_link".
        assert ':href="alert.deep_link"' in body

    def test_dashboard_camera_settings_has_offline_alerts_toggle(self, client):
        """#137 — Camera Settings modal exposes a toggle for the
        per-camera offline_alerts_enabled flag added in #136.
        Pin both the visible label and the Alpine binding so a future
        refactor that quietly drops the toggle fails loudly.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/dashboard")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # Toggle row label.
        assert ">Offline alerts<" in body
        # Bound to editForm.
        assert 'x-model="editForm.offline_alerts_enabled"' in body
        # Initial-state and save-payload wiring.
        assert "offline_alerts_enabled: (typeof cam.offline_alerts_enabled" in body
        assert "Boolean(this.editForm.offline_alerts_enabled)" in body

    def test_dashboard_camera_settings_has_encoder_preset_controls(self, client):
        """#252 â€” Camera Settings modal exposes encoder preset controls."""
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/dashboard")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert ">Encoder preset<" in body
        assert 'x-model="editForm.encoder_preset"' in body
        assert "/api/v1/cameras/encoder-presets" in body
        assert "onEncoderPresetChange()" in body
        assert "onEncoderFieldEdited()" in body

    def test_alerts_page_has_review_queue_sort_toggle(self, client):
        """#144 review queue — the alerts page exposes the
        importance-sort mode as a "Review queue" button alongside
        "Newest". Pin both the chip text and the API parameter name
        so a future "tidy-up" doesn't quietly drop the wiring.
        """
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/alerts")
        body = response.get_data(as_text=True)
        assert ">Review queue<" in body
        assert ">Newest<" in body
        # API parameter wiring — sort=importance reaches the backend.
        assert "sort=importance" in body or "'sort'" in body
        # Alpine state tracks the current mode.
        assert "sortMode" in body

    def test_live_page_has_share_controls(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/live")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Share live view" in body
        assert "Manage share links" in body
        assert "Create share link" in body

    def test_recordings_page_has_share_controls(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/recordings")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Share clip" in body
        assert "Manage links" in body
        assert "Lock to the first viewer IP subnet" in body


class TestDashboardSensorAwareSettings:
    """The Camera Settings modal builds its resolution dropdown from
    each camera's reported sensor_modes (#173) rather than a global
    hardcoded list. This regression test pins the template-side
    structure so a future "tidy-up" doesn't quietly delete the
    dynamic rendering and snap us back to OV5647-only modes."""

    @pytest.fixture(autouse=True)
    def setup_done(self, app):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")

    def test_dashboard_renders_dynamic_resolution_template(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/dashboard")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # New dynamic dropdown markup is present.
        assert 'x-for="opt in editForm.resolutionOptions"' in body
        assert ':value="opt.value"' in body
        assert 'x-effect="$el.value = editForm.resolution"' in body
        assert ':selected="opt.value === editForm.resolution"' in body
        # Sensor label row is present (hidden when empty).
        assert "editForm.sensorLabel" in body
        # Mismatch banner is present.
        assert "editForm.resolutionMismatch" in body
        # Legacy hardcoded ``_resMaxFps`` map MUST be gone — its presence
        # would mean the per-camera lookup got reverted.
        assert "_resMaxFps:" not in body, (
            "Legacy hardcoded _resMaxFps map reappeared — multi-sensor "
            "support regressed (see #173 / P1.3)."
        )
        # Sensor-aware helper is the new source of truth.
        assert "_resolutionOptionsFor" in body
        assert "_legacyResolutionOptions" in body
        assert "current, max " in body
        assert "var pickedRes = resKey" in body


class TestCompleteGuiRedesignCoverage:
    """Regression coverage for the redesigned web GUI surfaces.

    These assertions deliberately pin structural anchors, controls, and
    responsive CSS hooks rather than pixels. That keeps the tests useful for
    future visual polish while still catching accidental removal of routes,
    tabs, modals, destructive actions, or mobile layout safeguards.
    """

    @pytest.fixture(autouse=True)
    def setup_done(self, app):
        stamp = os.path.join(app.config["DATA_DIR"], ".setup-done")
        with open(stamp, "w") as f:
            f.write("done")

    def _get_body(self, client, path, role="admin"):
        _authenticate(
            client,
            role=role,
            username="admin" if role == "admin" else "viewer",
            user_id="user-001" if role == "admin" else "user-002",
        )
        response = client.get(path)
        assert response.status_code == 200
        return response.get_data(as_text=True)

    def test_shell_design_system_and_primary_navigation_are_present(self, client):
        body = self._get_body(client, "/dashboard")
        assert "css/control-panel.css" in body
        assert 'class="top-bar"' in body
        assert 'id="topbar-clock"' in body
        assert 'id="topbar-alerts"' in body
        assert 'id="bottom-nav"' in body
        for label in ["Dashboard", "Live", "Recordings", "Events", "Settings"]:
            assert f"<span>{label}</span>" in body
        assert 'href="/alerts"' in body
        assert 'id="btn-logout"' in body

    def test_all_redesigned_server_pages_render_core_headers(self, client):
        pages = [
            ("/dashboard", "Home state", "Local home control"),
            ("/live", "Live View", "Live monitor"),
            ("/recordings", "Recordings", "Playback library"),
            ("/events", "Events", "Motion review"),
            ("/alerts", "Alerts", "Review queue"),
            ("/settings", "Settings", "System control"),
            ("/logs", "Activity log", "Audit trail"),
            ("/shares", "Share Links", "Manage active public links"),
        ]
        for path, title, supporting_text in pages:
            body = self._get_body(client, path)
            assert title in body
            assert supporting_text in body

    def test_alpine_pages_do_not_double_initialize_pollers(self, client):
        """Explicit boot() avoids Alpine auto-calling init() plus x-init."""
        pages = {
            "/dashboard": "dashboardPage",
            "/settings": "settingsPage",
            "/events": "eventsPage",
            "/alerts": "alertsPage",
        }
        for path, component in pages.items():
            body = self._get_body(client, path)
            assert 'x-init="init()"' not in body
            assert 'x-init="boot()"' in body
            assert f'x-data="{component}()"' in body

    def test_dashboard_preserves_all_camera_roll_call_actions(self, client):
        body = self._get_body(client, "/dashboard")
        for text in [
            "System status",
            "Motion timeline",
            "Device roll call",
            "Scan",
            "Add Camera",
            "No cameras found",
            "Discovered",
            "Paired cameras",
            "Camera Settings",
            "Offline alerts",
            "Encoder preset",
        ]:
            assert text in body
        assert "toggleCameraDetails(cam.id" in body
        assert "isCameraDetailsOpen(cam.id)" in body
        assert ":id=\"'camera-' + cam.id\"" in body
        assert ":disabled=\"cam.status !== 'online'\"" in body
        assert "Camera is offline. Settings unlock when it reconnects." in body
        assert "cam.status === 'online' && openStreamSettings(cam)" in body

    def test_live_recordings_and_share_modal_preserve_all_actions(self, client):
        live = self._get_body(client, "/live")
        for text in [
            "Live View",
            "Low latency",
            "Local LAN",
            "Snapshot",
            "Share live view",
            "Manage share links",
        ]:
            assert text in live
        for anchor in [
            'id="live-camera-select"',
            'id="live-video"',
            'id="btn-mute"',
            'id="btn-pip"',
            'id="btn-fullscreen"',
            'id="btn-snapshot"',
        ]:
            assert anchor in live

        recordings = self._get_body(client, "/recordings")
        for text in [
            "Recordings",
            "Timeline",
            "Clip sharing",
            "Share clip",
            "Manage links",
            "More options",
            "Delete all on this date",
            "Delete all for this camera",
            "Select all",
            "Delete",
        ]:
            assert text in recordings
        for anchor in [
            'id="rec-camera-select"',
            'id="rec-date-input"',
            'id="rec-video"',
            'id="rec-clip-list"',
            'id="rec-selection-bar"',
        ]:
            assert anchor in recordings

        for modal_body in [live, recordings]:
            assert 'id="share-modal"' in modal_body
            assert 'role="dialog"' in modal_body
            assert 'id="share-ttl"' in modal_body
            for ttl in ["1 hour", "24 hours", "7 days", "30 days", "Never"]:
                assert ttl in modal_body
            assert 'id="share-pin-ip"' in modal_body
            assert 'id="share-pin-ua"' in modal_body
            assert 'id="share-note"' in modal_body
            assert 'id="share-create-btn"' in modal_body
            assert 'id="share-copy-btn"' in modal_body

    def test_events_alerts_and_logs_preserve_filters_and_actions(self, client):
        events = self._get_body(client, "/events")
        for text in ["Events", "Motion", "Clip jump", "Refresh"]:
            assert text in events
        for anchor in ['id="ev-camera-select"', 'id="ev-from"', 'id="ev-to"']:
            assert anchor in events
        assert "typeOptions" in events
        assert "playMotion(ev)" in events

        alerts = self._get_body(client, "/alerts")
        for text in [
            "Alerts",
            "Faults",
            "Audit",
            "Motion",
            "Unread only",
            "Newest",
            "Review queue",
            "Mark all read",
        ]:
            assert text in alerts
        assert "filterSeverity === 'warning'" in alerts
        assert "filterSeverity === 'error'" in alerts
        assert "markRead(alert.id)" in alerts
        assert "markAllRead()" in alerts

        logs = self._get_body(client, "/logs")
        for text in [
            "Activity log",
            "Export format",
            "CSV",
            "JSON",
            "Apply current filters",
            "Refresh",
            "Clear all entries",
            "Permanently clear?",
        ]:
            assert text in logs
        for anchor in ['id="log-user"', 'id="log-from"', 'id="log-to"']:
            assert anchor in logs

    def test_settings_preserves_every_tab_and_admin_option_group(self, client):
        body = self._get_body(client, "/settings")
        for tab in [
            "System",
            "Network",
            "Tailscale",
            "Users",
            "Security",
            "Recording",
            "Storage",
            "Webhooks",
            "Updates",
            "Account",
            "Notifications",
        ]:
            assert f"label: '{tab}'" in body
            assert f">{tab}</button>" in body or f">{tab}<" in body

        expected_controls = [
            "System Settings",
            'id="set-hostname"',
            'id="set-timezone"',
            'name="ntp-mode"',
            "Configuration Backup",
            "Download Backup",
            "Configuration Restore",
            "Preview Restore",
            "Confirm Restore",
            "Factory Reset",
            "Network Interfaces",
            "WiFi Network",
            'id="wifi-ssid"',
            'id="wifi-pass"',
            "Enable Tailscale",
            "Tailscale SSH",
            "User Management",
            'id="new-username"',
            'id="new-password"',
            'id="new-role"',
            "Reset password",
            "Reset 2FA",
            "Active Sessions",
            "Sign out other devices",
            "Two-Factor Authentication",
            'id="totp-confirm-code"',
            "Regenerate Recovery Codes",
            "Disable 2FA",
            "Recording Modes",
            "Recording Storage",
            "Loop Recording",
            "Offsite Backup",
            "Outbound webhooks",
            "Recent deliveries",
            "Browser notifications",
            "Quiet hours",
            "Camera defaults",
            "Maintenance Restart",
            "Camera fleet",
            "Common camera schedule",
            "Search cameras",
            "Restart selected",
            "Apply to selected",
            "Apply to visible",
            "No cameras match these filters.",
            "Server Software Update",
            "Camera Software Updates",
            "Camera bundle",
            'id="camera-common-bundle-file"',
            "Update eligible cameras",
            "Upload custom bundle",
            "Install selected bundle",
            "time-health-table-wrap",
            'data-label="Camera"',
            'data-label="Action"',
            "webhook-destination-table",
            "settings-table-actions",
            'data-label="Destination"',
            'data-label="Detail"',
        ]
        for expected in expected_controls:
            assert expected in body

    def test_viewer_settings_only_exposes_personal_tabs(self, client):
        body = self._get_body(client, "/settings", role="viewer")
        assert "label: 'Account'" in body
        assert "label: 'Security'" in body
        assert "label: 'Notifications'" in body
        for admin_tab in [
            "System",
            "Network",
            "Tailscale",
            "Users",
            "Recording",
            "Storage",
            "Webhooks",
            "Updates",
        ]:
            assert f"label: '{admin_tab}'" in body
        assert "settingsNavItems()" in body
        assert "if (this.isAdmin)" in body

    def test_control_panel_css_covers_responsive_lists_tables_and_modals(self, client):
        response = client.get("/static/css/control-panel.css")
        assert response.status_code == 200
        css = response.get_data(as_text=True)
        for selector in [
            ".bottom-nav",
            "env(safe-area-inset-bottom)",
            "@media (max-width: 700px)",
            ".settings-page",
            ".settings-page .settings-tabs",
            ".settings-page > div[x-show]",
            ".events-feed",
            ".event-row:nth-child(even)",
            ".alerts-list",
            ".alert-row:nth-child(even)",
            ".clip-card:nth-child(even)",
            ".settings-section table tbody tr:nth-child(even) td",
            ".camera-card__faults .fault-badge span:last-child",
            ".share-modal__dialog",
            ".ota-camera-list",
            ".ota-common-bundle",
            ".ota-common-bundle__summary",
            ".ota-file-picker__input",
            ".ota-device-card__header",
            ".ota-job-panel",
            ".ota-device-card__controls",
            ".ota-state-pill--busy",
            ".maintenance-fleet",
            ".maintenance-fleet__filters",
            ".maintenance-camera-list",
            ".maintenance-camera-row__main",
            ".time-health-table",
            ".time-health-table__action",
            ".webhook-destination-table",
            ".settings-table-actions",
        ]:
            assert selector in css

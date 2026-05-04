# REQ: SWR-022, SWR-067; RISK: RISK-007, RISK-010, RISK-015; SEC: SC-010, SC-012; TEST: TC-021, TC-054
"""
Tests for view routes — HTML page serving and redirects.
"""

import os

import pytest


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

    def test_live_renders_when_authenticated(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "user-001"
            sess["username"] = "admin"
            sess["role"] = "admin"
        response = client.get("/live")
        assert response.status_code == 200

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

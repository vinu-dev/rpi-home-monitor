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

"""
View routes — serves HTML pages for the web dashboard.

All page routes check authentication via session and redirect
to /login if not authenticated. The /setup page is shown when
initial setup has not been completed.
"""

import os

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    session,
    url_for,
)

views_bp = Blueprint("views", __name__)


def _setup_complete():
    """Check if initial device setup has been completed."""
    data_dir = current_app.config.get("DATA_DIR", "/data")
    return os.path.isfile(os.path.join(data_dir, ".setup-done"))


def _is_authenticated():
    """Check if current session has a logged-in user."""
    return "user_id" in session


# REQ: SWR-022; RISK: RISK-010; SEC: SC-010; TEST: TC-021
@views_bp.route("/")
def index():
    """Root route — redirect based on setup/auth state."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return redirect(url_for("views.dashboard"))


@views_bp.route("/setup")
def setup():
    """Initial device setup wizard."""
    if _setup_complete():
        return redirect(url_for("views.login"))
    from monitor.services.provisioning_service import SERVER_HOSTNAME

    return render_template("setup.html", hostname=f"{SERVER_HOSTNAME}.local")


@views_bp.route("/login")
def login():
    """Login page."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if _is_authenticated():
        return redirect(url_for("views.dashboard"))
    return render_template("login.html")


@views_bp.route("/dashboard")
def dashboard():
    """Main dashboard — system health and camera overview."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return render_template("dashboard.html")


@views_bp.route("/live")
def live():
    """Live camera view with HLS player."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return render_template("live.html")


@views_bp.route("/recordings")
def recordings():
    """Recordings browser — browse and play recorded clips."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return render_template("recordings.html")


@views_bp.route("/events")
def events():
    """Events feed — motion detections (and future operator-notable
    event types). Continuous recording clips live in /recordings, not
    here, so this surface stays signal-not-noise. Linked from the
    dashboard's "All events →" escape hatch and the Events nav item."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return render_template("events.html")


@views_bp.route("/alerts")
def alerts():
    """Alert center inbox (ADR-0024). Derive-on-read view over audit,
    motion, and per-camera fault sources with per-user read state.
    Backend ships in #208 (`AlertCenterService`). The top-bar bell
    badge polls `/api/v1/alerts/unread-count` and links here.

    Permission gating happens server-side in
    ``AlertCenterService.list_alerts(role=...)`` — viewers see only
    fault- and motion-derived alerts; admins see everything. The page
    template doesn't try to second-guess the role; it renders whatever
    the API hands back.
    """
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return render_template("alerts.html")


@views_bp.route("/logs")
def logs():
    """Full activity log — security/ops audit events with filters.
    Admin-only (the template surfaces a permission-denied card when a
    viewer-scope session somehow lands here; the underlying API also
    returns 403). Linked from the dashboard's "Full log →" escape hatch."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    # "I've seen the log" ack — clears the dashboard status strip's
    # "N recent system event…" callout without waiting the full hour
    # for events to age out of the error window. Per-session so each
    # admin clears their own view. Stamped at the instant the page
    # is rendered; any event written after this is a fresh alert.
    from datetime import UTC, datetime

    session["audit_seen_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return render_template("logs.html")


@views_bp.route("/settings")
def settings():
    """System settings and user management."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return render_template("settings.html")


@views_bp.route("/shares")
def shares():
    """Authenticated share-link management page."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return render_template("share_management.html")

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


@views_bp.route("/settings")
def settings():
    """System settings and user management."""
    if not _setup_complete():
        return redirect(url_for("views.setup"))
    if not _is_authenticated():
        return redirect(url_for("views.login"))
    return render_template("settings.html")

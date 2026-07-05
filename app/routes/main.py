"""Main blueprint: homepage redirect and the protected dashboard shell.

The dashboard itself stays static for now — this route only proves the
auth guard and session identity work end to end.
"""

from flask import Blueprint, redirect, render_template, session, url_for

from .auth import login_required

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    """Homepage: authenticated users land on the dashboard, everyone else on login."""
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")

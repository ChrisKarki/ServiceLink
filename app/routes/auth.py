"""Authentication blueprint — implements the login half of FR-1.1 / FR-1.2.

Implemented here:
  - Email + password login with bcrypt verification (FR-1.1, NFR-S1)
  - 8-hour ABSOLUTE session timeout (FR-1.1) — see note on login_required
  - Account status enforcement: PendingApproval and Suspended accounts
    cannot sign in (FR-1.2)
  - Role-based access decorator for the four fixed roles (FR-1.2)
  - lastLoginAt recorded on successful sign-in

Explicitly deferred to a later build (stub routes below, so the scope
gap is visible instead of hidden):
  - TOTP multi-factor authentication (FR-1.1)
  - Self-registration with Administrator approval workflow (FR-1.2)
  - Password reset via email token (FR-1.2)
"""

import time
from functools import wraps

import bcrypt
from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)

from ..db import execute, query_one

bp = Blueprint("auth", __name__)

SESSION_MAX_AGE_SECONDS = 8 * 60 * 60  # FR-1.1: 8-hour absolute timeout

# Dummy hash of a random value. When a login email doesn't exist we still
# run bcrypt against this, so a wrong-email attempt takes the same time as
# a wrong-password attempt (no user enumeration via response timing).
_DUMMY_HASH = b"$2b$12$a2Vm4tpPi571XZyIy0KQ1utw5qT/fo3Ueo/Oc.quv..41eyj1vSMi"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def login_required(view):
    """Require an authenticated, non-expired session.

    Flask's PERMANENT_SESSION_LIFETIME is a *sliding* window by default
    (the cookie refreshes on each request), but FR-1.1 requires an
    ABSOLUTE timeout regardless of activity. So the login time is stored
    in the session and checked here on every protected request.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login", next=request.path))
        if time.time() - session.get("auth_at", 0) > SESSION_MAX_AGE_SECONDS:
            session.clear()
            flash("Your session has expired after 8 hours. Please sign in again.", "info")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    """Restrict a route to specific roles, e.g. @roles_required('Manager', 'Administrator')."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                flash("You do not have permission to access that page.", "error")
                return redirect(url_for("main.dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = query_one(
            "SELECT userID, email, passwordHash, firstName, lastName, role, status"
            "  FROM User WHERE email = %s",
            (email,),
        )

        stored_hash = user["passwordHash"].encode() if user else _DUMMY_HASH
        password_ok = bcrypt.checkpw(password.encode(), stored_hash)

        if not user or not password_ok:
            # One generic message for both cases — never reveal whether
            # the email exists.
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html"), 401

        # Credentials are correct — now enforce account status (FR-1.2).
        if user["status"] == "PendingApproval":
            flash("Your account is awaiting Administrator approval.", "warning")
            return render_template("auth/login.html"), 403
        if user["status"] == "Suspended":
            flash("Your account has been suspended. Contact an Administrator.", "error")
            return render_template("auth/login.html"), 403

        # Success: rotate the session, then establish identity.
        session.clear()
        session.permanent = True
        session["user_id"] = user["userID"]
        session["name"] = f"{user['firstName']} {user['lastName']}"
        session["initials"] = (user["firstName"][:1] + user["lastName"][:1]).upper()
        session["role"] = user["role"]
        session["auth_at"] = time.time()

        execute("UPDATE User SET lastLoginAt = NOW() WHERE userID = %s",
                (user["userID"],))

        flash("Logged in successfully", "success")
        next_url = request.args.get("next")
        # Only follow relative paths — never an absolute URL (open-redirect guard).
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html")


@bp.get("/logout")
def logout():
    session.clear()
    flash("Signed out", "info")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Deferred scope — visible stubs, not silent gaps
# ---------------------------------------------------------------------------

@bp.get("/register")
def register():
    flash("Self-registration is scheduled for a later build (FR-1.2).", "info")
    return redirect(url_for("auth.login"))


@bp.get("/forgot-password")
def forgot_password():
    flash("Password reset via email token is scheduled for a later build (FR-1.2).", "info")
    return redirect(url_for("auth.login"))
# Hiten
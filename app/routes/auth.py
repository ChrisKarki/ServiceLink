"""Authentication blueprint — FR-1.1 and FR-1.2 in full.

Previously this module implemented password login only, with TOTP,
self-registration, and password reset left as visible stubs. All three are
now built.

FR-1.1 — Authentication
    * Email + bcrypt password login (NFR-S1)
    * TOTP multi-factor: QR enrolment, 6-digit challenge, drift tolerance
    * MFA is mandatory by default; Administrators may disable it system-wide
      at /admin/security (SystemSetting.mfa_required)
    * 8-hour ABSOLUTE session timeout — see login_required

FR-1.2 — Account lifecycle
    * Self-registration; every new account is created EndUser /
      PendingApproval. Role elevation is an Administrator action only
      (admin.change_role) — registration can never mint privilege.
    * Account status enforcement: PendingApproval and Suspended cannot sign in
    * Password reset by emailed one-time code. A completed reset ALSO clears
      TOTP enrolment, because someone who has lost their password has usually
      lost the authenticator with it.

THE HALF-AUTHENTICATED SESSION
------------------------------
Between a correct password and a correct TOTP code the user is not logged in.
That intermediate state lives under `pending_*` session keys and is NEVER
`user_id` — every guard in the application keys off `user_id`, so a
half-authenticated session cannot reach a protected route no matter how the
MFA step is abandoned. It expires on its own after PENDING_MAX_AGE.

ENUMERATION
-----------
Registration and password-reset both answer identically whether or not the
address exists. Login already used one generic failure message and a dummy
bcrypt comparison to equalise response timing; both are preserved.
"""

import time
from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, session, url_for)

from ..db import execute, query_all, query_one
from ..services import notify, security
from ..services.audit import log_action

bp = Blueprint("auth", __name__)

SESSION_MAX_AGE_SECONDS = 8 * 60 * 60  # FR-1.1: 8-hour absolute timeout
PENDING_MAX_AGE = 5 * 60               # password OK -> MFA done, 5 minutes
MFA_MAX_ATTEMPTS = 5

# Dummy hash of a random value. When a login email doesn't exist we still
# run bcrypt against this, so a wrong-email attempt takes the same time as
# a wrong-password attempt (no user enumeration via response timing).
_DUMMY_HASH = "$2b$12$a2Vm4tpPi571XZyIy0KQ1utw5qT/fo3Ueo/Oc.quv..41eyj1vSMi"


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
    """Restrict a route to specific roles, e.g. @roles_required('Manager', 'Administrator').

    An authenticated user whose role is not permitted gets a hard 403.
    We deliberately do NOT redirect: a redirect reports "wrong place, here's
    somewhere else", while the requirement (FR-1.2, and the H1.1 acceptance
    criterion) is that the request is *denied*. A 403 is also what Katie's
    automation asserts against, and what a security review expects to see.
    """
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _safe_next(raw):
    """Only follow relative paths — never an absolute URL, and never a
    protocol-relative '//host' (open-redirect guard)."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return None


def _begin_pending(user, next_url):
    """Park a password-verified user in the half-authenticated state."""
    session.clear()
    session["pending_uid"] = user["userID"]
    session["pending_email"] = user["email"]
    session["pending_at"] = time.time()
    session["pending_next"] = next_url or ""
    session["mfa_tries"] = 0


def _pending_user():
    """Return the half-authenticated user row, or None if absent/expired."""
    uid = session.get("pending_uid")
    if not uid:
        return None
    if time.time() - session.get("pending_at", 0) > PENDING_MAX_AGE:
        session.clear()
        return None
    return query_one(
        "SELECT userID, email, passwordHash, totpSecret, mfaEnabled,"
        "       firstName, lastName, role, status"
        "  FROM User WHERE userID = %s", (uid,))


def _complete_login(user):
    """Establish the real identity. Session is rotated first so a fixated
    pre-login cookie cannot be reused (NFR-S2)."""
    next_url = _safe_next(session.get("pending_next"))
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
    return redirect(next_url or url_for("main.dashboard"))


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = query_one(
            "SELECT userID, email, passwordHash, totpSecret, mfaEnabled,"
            "       firstName, lastName, role, status"
            "  FROM User WHERE email = %s",
            (email,))

        stored_hash = user["passwordHash"] if user else _DUMMY_HASH
        password_ok = security.check_password(password, stored_hash)

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

        next_url = _safe_next(request.args.get("next"))

        # --- MFA decision (FR-1.1) ---------------------------------------
        # Already enrolled -> challenge, regardless of the system setting.
        # Turning MFA off must not silently weaken accounts that already
        # have a second factor; it only stops NEW enrolments being forced.
        if user["totpSecret"]:
            _begin_pending(user, next_url)
            return redirect(url_for("auth.mfa_challenge"))

        # Not enrolled. Enrol now if the system requires MFA globally, OR if
        # this individual account has User.mfaEnabled set. The per-user flag
        # lets an Administrator require a second factor on privileged accounts
        # while MFA stays optional for the wider user base.
        if security.mfa_required() or user["mfaEnabled"]:
            _begin_pending(user, next_url)
            return redirect(url_for("auth.mfa_setup"))

        # Not enrolled, MFA optional -> straight in.
        session["pending_next"] = next_url or ""
        return _complete_login(user)

    return render_template("auth/login.html")


@bp.get("/logout")
def logout():
    session.clear()
    flash("Signed out", "info")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# MFA challenge — existing enrolment
# ---------------------------------------------------------------------------

@bp.route("/login/mfa", methods=["GET", "POST"])
def mfa_challenge():
    user = _pending_user()
    if user is None:
        flash("Your sign-in attempt timed out. Please sign in again.", "info")
        return redirect(url_for("auth.login"))
    if not user["totpSecret"]:
        return redirect(url_for("auth.mfa_setup"))

    if request.method == "POST":
        if session.get("mfa_tries", 0) >= MFA_MAX_ATTEMPTS:
            session.clear()
            flash("Too many incorrect codes. Please sign in again.", "error")
            return redirect(url_for("auth.login"))

        if security.verify_totp(user["totpSecret"], request.form.get("code")):
            return _complete_login(user)

        session["mfa_tries"] = session.get("mfa_tries", 0) + 1
        remaining = MFA_MAX_ATTEMPTS - session["mfa_tries"]
        flash(f"Incorrect code. {remaining} attempt"
              f"{'' if remaining == 1 else 's'} remaining.", "error")
        return render_template("auth/mfa_challenge.html",
                               email=user["email"]), 401

    return render_template("auth/mfa_challenge.html", email=user["email"])


# ---------------------------------------------------------------------------
# MFA enrolment — QR + confirm
# ---------------------------------------------------------------------------

@bp.route("/login/mfa/setup", methods=["GET", "POST"])
def mfa_setup():
    user = _pending_user()
    if user is None:
        flash("Your sign-in attempt timed out. Please sign in again.", "info")
        return redirect(url_for("auth.login"))
    if user["totpSecret"]:
        return redirect(url_for("auth.mfa_challenge"))

    # The candidate secret lives in the session until the user proves they
    # scanned it. Persisting at QR-render time would lock out anyone who
    # closed the tab halfway through.
    secret = session.get("pending_secret")
    if not secret:
        secret = security.new_totp_secret()
        session["pending_secret"] = secret

    if request.method == "POST":
        if security.verify_totp(secret, request.form.get("code")):
            security.enrol_totp(user["userID"], secret)
            log_action(user["userID"], "User", user["userID"], "Update",
                       changes={"totpSecret": ("not enrolled", "enrolled")},
                       ip=request.remote_addr)
            session.pop("pending_secret", None)
            flash("Two-factor authentication is now active on your account.",
                  "success")
            return _complete_login(user)
        flash("That code didn't match. Check your authenticator app and "
              "try the current code.", "error")

    return render_template("auth/mfa_enroll.html",
                           qr=security.qr_data_uri(secret, user["email"]),
                           secret=secret, email=user["email"])


# ---------------------------------------------------------------------------
# Self-registration (FR-1.2)
# ---------------------------------------------------------------------------

@bp.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        first = request.form.get("firstName", "").strip()
        last = request.form.get("lastName", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = security.validate_password(password, confirm)
        if not first:
            errors["firstName"] = "First name is required."
        if not last:
            errors["lastName"] = "Last name is required."
        if "@" not in email or "." not in email.split("@")[-1]:
            errors["email"] = "Enter a valid email address."

        if errors:
            return render_template("auth/register.html", errors=errors,
                                   form=request.form), 400

        # Enumeration guard: an already-registered address produces the SAME
        # confirmation page as a new one. The existing account holder is
        # emailed instead, which is both safer and more useful to them.
        existing = query_one("SELECT userID FROM User WHERE email = %s",
                             (email,))
        if existing:
            try:
                notify.send(existing["userID"],
                            "Registration attempt on your ServiceLink account",
                            "Someone tried to register an account with this "
                            "email address. Your existing account is "
                            "unchanged. If this wasn't you, no action is "
                            "needed — but consider resetting your password.")
            except Exception:
                pass
            return render_template("auth/register_done.html", email=email)

        # Every self-registered account starts at the lowest privilege and
        # cannot sign in until an Administrator approves it. Role and status
        # are literals here, never taken from the form — registration must
        # not be a privilege-escalation path.
        user_id = execute(
            "INSERT INTO User (firstName, lastName, email, passwordHash,"
            "                  role, status)"
            " VALUES (%s, %s, %s, %s, 'EndUser', 'PendingApproval')",
            (first, last, email, security.hash_password(password)))

        log_action(user_id, "User", user_id, "Create",
                   changes={"role": (None, "EndUser"),
                            "status": (None, "PendingApproval")},
                   ip=request.remote_addr)

        for admin in query_all(
                "SELECT userID FROM User WHERE role = 'Administrator'"
                "   AND status = 'Active'"):
            try:
                notify.send(admin["userID"],
                            "New ServiceLink account awaiting approval",
                            f"{first} {last} ({email}) registered and is "
                            f"pending approval. Review it under "
                            f"Administration -> Users.")
            except Exception:
                pass

        return render_template("auth/register_done.html", email=email)

    return render_template("auth/register.html", errors={}, form={})


# ---------------------------------------------------------------------------
# Password reset by one-time code (FR-1.2)
# ---------------------------------------------------------------------------

@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = query_one("SELECT userID, status FROM User WHERE email = %s",
                         (email,))

        # Suspended accounts get no code — a reset must not become a way
        # back in for an account an Administrator has closed.
        if user and user["status"] != "Suspended":
            code = security.issue_reset_otp(user["userID"],
                                            ip=request.remote_addr)
            try:
                notify.send(
                    user["userID"],
                    "Your ServiceLink password reset code",
                    f"Your one-time code is {code}\n\n"
                    f"It expires in {security.OTP_TTL_MINUTES} minutes and "
                    f"can be used once.\n\n"
                    f"Completing a reset also removes two-factor "
                    f"authentication from your account — you will be asked "
                    f"to set up your authenticator app again on your next "
                    f"sign-in.\n\n"
                    f"If you didn't request this, you can ignore this email; "
                    f"your password has not changed.")
            except Exception:
                pass

        # Identical response either way — never confirm whether an address
        # is registered.
        return redirect(url_for("auth.reset_password", email=email))

    return render_template("auth/forgot_password.html")


@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        code = request.form.get("code", "")
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = security.validate_password(password, confirm)
        if errors:
            return render_template("auth/reset_password.html", errors=errors,
                                   email=email), 400

        user = query_one("SELECT userID, status FROM User WHERE email = %s",
                         (email,))
        ok = False
        if user and user["status"] != "Suspended":
            ok, _reason = security.verify_reset_otp(user["userID"], code)

        if not ok:
            # One message for every failure mode — wrong code, expired code,
            # unknown address, too many attempts all look the same.
            flash("That code is not valid or has expired. Request a new one.",
                  "error")
            return render_template("auth/reset_password.html", errors={},
                                   email=email), 400

        execute("UPDATE User SET passwordHash = %s WHERE userID = %s",
                (security.hash_password(password), user["userID"]))
        # A password reset also resets MFA: whoever lost the password has
        # usually lost the authenticator too. Next sign-in re-enrols.
        security.reset_totp(user["userID"])

        log_action(user["userID"], "User", user["userID"], "Update",
                   changes={"passwordHash": ("(reset via one-time code)",
                                             "(updated)"),
                            "totpSecret": ("enrolled", "reset")},
                   ip=request.remote_addr)
        try:
            notify.send(user["userID"], "Your ServiceLink password was changed",
                        "Your password has been reset and two-factor "
                        "authentication has been removed. You'll be asked to "
                        "set up your authenticator app again next time you "
                        "sign in.\n\nIf this wasn't you, contact an "
                        "Administrator immediately.")
        except Exception:
            pass

        session.clear()
        flash("Password updated. Sign in with your new password — you'll be "
              "asked to set up two-factor authentication again.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", errors={},
                           email=request.args.get("email", ""))

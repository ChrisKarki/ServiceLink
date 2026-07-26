"""In-app notification centre (FR-2.5) — the topbar bell.

services.notify.send() writes a Notification row for every event it fires,
so this blueprint only has to read, mark read, and render. There is no
separate list of "things the bell knows about": the FR-2.5 registry in
notify.py is the single source of truth for all three channels.

The context processor below runs on EVERY authenticated page render, because
base.html draws the bell on every page. It is therefore kept to two indexed
counting/limit queries and is skipped entirely for anonymous requests.
"""

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)

from ..db import execute, query_all, query_one
from .auth import login_required

bp = Blueprint("notifications", __name__, url_prefix="/notifications")

DROPDOWN_LIMIT = 6      # entries shown under the bell
PAGE_LIMIT = 50         # entries on the full page
BADGE_CAP = 99          # unread counts above this render as "99+"


# ---------------------------------------------------------------------------
# Bell state — injected into every template
# ---------------------------------------------------------------------------

@bp.app_context_processor
def inject_bell():
    """Supply notif_unread / notif_recent to base.html.

    Returns empty values rather than raising when the user is anonymous or
    the Notification table has not been migrated yet. base.html is shared by
    every authenticated page, so an exception here would take down the whole
    application rather than one feature.
    """
    if "user_id" not in session:
        return {"notif_unread": 0, "notif_recent": [], "notif_badge": ""}

    try:
        uid = session["user_id"]
        unread = query_one(
            "SELECT COUNT(*) AS n FROM Notification"
            " WHERE userID = %s AND readAt IS NULL", (uid,))["n"]
        recent = query_all(
            "SELECT notificationID, subject, body, link, readAt, createdAt"
            "  FROM Notification WHERE userID = %s"
            f" ORDER BY createdAt DESC LIMIT {DROPDOWN_LIMIT}", (uid,))
    except Exception:
        return {"notif_unread": 0, "notif_recent": [], "notif_badge": ""}

    return {
        "notif_unread": unread,
        "notif_recent": recent,
        "notif_badge": (f"{BADGE_CAP}+" if unread > BADGE_CAP
                        else str(unread) if unread else ""),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.get("/")
@login_required
def index():
    """Full history for the signed-in user. Scoped by userID in SQL, so one
    user can never read another's notifications (FR-5.1)."""
    rows = query_all(
        "SELECT notificationID, subject, body, link, readAt, createdAt"
        "  FROM Notification WHERE userID = %s"
        f" ORDER BY createdAt DESC LIMIT {PAGE_LIMIT}", (session["user_id"],))
    unread = sum(1 for r in rows if r["readAt"] is None)
    return render_template("notifications/index.html", items=rows,
                           unread=unread, limit=PAGE_LIMIT)


@bp.get("/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    """Mark one notification read, then follow its link.

    The WHERE clause carries userID, so a guessed id belonging to someone
    else updates zero rows and 404s rather than leaking its existence.
    """
    row = query_one(
        "SELECT notificationID, link FROM Notification"
        " WHERE notificationID = %s AND userID = %s",
        (notification_id, session["user_id"]))
    if row is None:
        flash("That notification is no longer available.", "info")
        return redirect(url_for("notifications.index"))

    execute("UPDATE Notification SET readAt = NOW()"
            " WHERE notificationID = %s AND userID = %s AND readAt IS NULL",
            (notification_id, session["user_id"]))

    return redirect(_safe_link(row["link"]) or url_for("notifications.index"))


@bp.post("/read-all")
@login_required
def read_all():
    execute("UPDATE Notification SET readAt = NOW()"
            " WHERE userID = %s AND readAt IS NULL", (session["user_id"],))
    flash("All notifications marked as read.", "success")
    return redirect(request.referrer or url_for("notifications.index"))


def _safe_link(raw):
    """Only follow relative paths — never an absolute URL, never a
    protocol-relative '//host'.

    These links are written by our own code, so this is belt-and-braces
    rather than a live hole. It stays because a future call site passing a
    user-influenced string would otherwise turn the bell into an open
    redirect, and the same guard already protects auth's `next` parameter.
    """
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return None

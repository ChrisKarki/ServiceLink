"""Tickets blueprint — cards P1.1 / P1.2 / P1.3 plus the H2.1 linking seam.

Implemented here:
  P1.1  GET/POST /tickets/new       — submission with server-side validation
                                      (NFR-S4), status New, audit-logged,
                                      EX-1: errors preserve form data
  P1.2  round-robin auto-assignment — on submit, next active Technician by
                                      per-category rotation; New→Assigned;
                                      notified via services.notify (C0.2).
                                      AF-1: no active Technician → stays New
                                      in the unassigned queue, Managers
                                      notified
  P1.3  GET /tickets, /tickets/<id> — role-scoped visibility (FR-5.1):
                                      End Users see only their own;
                                      Technicians see assigned + unassigned
                                      queue; Managers/Admins see everything.
                                      Out-of-scope URL access → 403
  H2.1  POST /tickets/<id>/resources         — link a Resource (UC-03)
        POST /tickets/<id>/resources/unlink  — unlink
                                      AF-2: duplicate link (composite PK)
                                      surfaces as a friendly message;
                                      EX-2: Disposed / Lost-Missing resources
                                      link with an explicit warning.

  P2.1  POST /tickets/<id>/comments  — Internal (staff-only) vs Public
                                      comments (FR-2.4). Internal notes are
                                      excluded from the End User query itself,
                                      never merely hidden in the template, so
                                      an End User response carries zero internal
                                      content. A Public comment notifies the
                                      submitter (FR-2.5).

Deferred (visible stubs in the detail template, not silent gaps):
  P1.4  status transitions, resolvedAt, resolution summary
  P2.x  attachments

All mutations go through services.audit.log_action — no local audit INSERTs
(C0.1 contract). Every user-supplied value is bound as a parameter (NFR-S4).
"""

from datetime import datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, session, url_for)
from mysql.connector.errors import IntegrityError

from ..db import execute, query_all, query_one
from ..services.audit import log_action
from ..services.notify import send as notify
from .auth import login_required, roles_required

# No url_prefix: /health must stay at its original path (Katie's smoke check
# hits it), so every ticket route spells out /tickets explicitly.
bp = Blueprint("tickets", __name__)

STAFF = ("Technician", "Manager", "Administrator")
PRIORITIES = ("Low", "Medium", "High", "Critical")

STATUS_LABELS = {
    "New": "New", "Assigned": "Assigned", "InProgress": "In Progress",
    "WaitingOnUser": "Waiting on User", "Resolved": "Resolved", "Closed": "Closed",
}

# P1.4 (FR-2.2, UC-02) — the legal six-state lifecycle. A transition is legal
# only if the target is in TRANSITIONS[current]. This is the single source of
# truth enforced server-side; the detail template only *offers* these same
# moves, but the endpoint re-checks every request regardless of the UI.
#   New→InProgress is allowed only by auto-claim (a Technician taking an
#   unassigned ticket); Assigned/InProgress/Resolved all require an assignee.
#   Resolved→InProgress reopens (clears resolvedAt); Resolved→Closed finalizes.
TRANSITIONS = {
    "New":           {"Assigned", "InProgress"},
    "Assigned":      {"InProgress"},
    "InProgress":    {"WaitingOnUser", "Resolved"},
    "WaitingOnUser": {"InProgress", "Resolved"},
    "Resolved":      {"Closed", "InProgress"},
    "Closed":        set(),  # terminal
}

# States that cannot exist without a technician on the ticket.
_REQUIRES_ASSIGNEE = ("Assigned", "InProgress", "Resolved")

# Badge presentation — identical mapping to main.py so a ticket looks the
# same on the dashboard and in this module (Arshdeep's classes, untouched).
_MUTED = "border: 1px solid var(--panel-border); color: var(--text-secondary);"
_WARN = "border: 1px solid var(--warning-color); color: var(--warning-color);"
STATUS_BADGES = {
    "New":           {"cls": "badge", "style": _MUTED},
    "Assigned":      {"cls": "badge badge-status-open", "style": ""},
    "InProgress":    {"cls": "badge badge-status-open", "style": ""},
    "WaitingOnUser": {"cls": "badge", "style": _WARN},
    "Resolved":      {"cls": "badge badge-status-resolved", "style": ""},
    "Closed":        {"cls": "badge", "style": _MUTED},
}
PRIORITY_BADGES = {
    "Critical": {"cls": "badge badge-priority-high", "style": ""},
    "High":     {"cls": "badge badge-priority-high", "style": ""},
    "Medium":   {"cls": "badge", "style": _WARN},
    "Low":      {"cls": "badge", "style": _MUTED},
}

_TICKET_SELECT = (
    "SELECT t.*, c.name AS categoryName,"
    "       CONCAT(s.firstName, ' ', s.lastName) AS submitterName,"
    "       CONCAT(a.firstName, ' ', a.lastName) AS assigneeName"
    "  FROM Ticket t"
    "  JOIN Category c ON c.categoryID = t.categoryID"
    "  JOIN User s     ON s.userID = t.submittedByUserID"
    "  LEFT JOIN User a ON a.userID = t.assignedToUserID")


@bp.get("/health")
def health():
    # Deliberately touches no DB, so it proves app wiring independent of MariaDB.
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# P1.1 — Submission (UC-01)
# ---------------------------------------------------------------------------

@bp.route("/tickets/new", methods=["GET", "POST"])
@login_required
def create_ticket():
    categories = query_all(
        "SELECT categoryID, name FROM Category WHERE isActive = TRUE ORDER BY name")

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category_id = request.form.get("category_id", "").strip()
        priority = request.form.get("priority", "").strip()

        errors = {}
        if not title:
            errors["title"] = "Summary / Title is required."
        elif len(title) > 150:
            errors["title"] = "Title must be 150 characters or fewer."
        if not description:
            errors["description"] = "Detailed Description is required."
        if not category_id:
            errors["category_id"] = "Please select a Category."
        elif not any(str(c["categoryID"]) == category_id for c in categories):
            errors["category_id"] = "Selected category is not valid."
        if not priority:
            errors["priority"] = "Please select a Priority level."
        elif priority not in PRIORITIES:
            errors["priority"] = "Invalid priority selection."

        # EX-1: validation errors re-render the form with entered data intact.
        if errors:
            flash("Please correct the highlighted validation errors.", "error")
            return render_template("tickets/new.html", categories=categories,
                                   priorities=PRIORITIES, form=request.form,
                                   errors=errors), 400

        # UC-01 main flow: ticket is born in status New, audit-logged.
        ticket_id = execute(
            "INSERT INTO Ticket (title, description, categoryID, priority,"
            "  status, submittedByUserID, createdAt)"
            " VALUES (%s, %s, %s, %s, 'New', %s, NOW())",
            (title, description, int(category_id), priority, session["user_id"]))

        log_action(session["user_id"], "Ticket", ticket_id, "Create",
                   ip=request.remote_addr)

        # P1.2 — hand the new ticket to the rotation.
        assigned = _auto_assign(ticket_id, int(category_id), title, priority)

        if assigned:
            flash(f"Ticket #{ticket_id} created and assigned to {assigned}.", "success")
        else:
            # AF-1: valid ticket, no route — it waits in the unassigned queue.
            flash(f"Ticket #{ticket_id} created. No technician is currently "
                  "available; it has been placed in the unassigned queue.", "warning")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    return render_template("tickets/new.html", categories=categories,
                           priorities=PRIORITIES, form={}, errors={})


# ---------------------------------------------------------------------------
# P1.2 — Round-robin auto-assignment (FR-2.3)
# ---------------------------------------------------------------------------

def _auto_assign(ticket_id, category_id, title, priority):
    """Assign to the next active Technician in the category rotation.

    Rotation rule: among active Technicians, pick the one whose most recent
    assignment IN THIS CATEGORY is oldest (never-assigned sorts first).
    That is a fair round-robin without storing a rotation pointer, and it
    directly satisfies the P1.2 acceptance criterion: two consecutive
    submissions in the same category land on different technicians.

    Note: the 12-relation schema has no Technician↔Category mapping table,
    so "active Technician in the category" (UC-01 AF-1) degrades to "any
    active Technician" — AF-1 therefore triggers when zero technicians are
    active. Documented deviation; the rotation itself is still per-category.

    Returns the assignee's display name, or None on AF-1.
    """
    tech = query_one(
        "SELECT u.userID, CONCAT(u.firstName, ' ', u.lastName) AS name,"
        "       (SELECT MAX(t.createdAt) FROM Ticket t"
        "         WHERE t.assignedToUserID = u.userID"
        "           AND t.categoryID = %s) AS lastAssigned"
        "  FROM User u"
        " WHERE u.role = 'Technician' AND u.status = 'Active'"
        " ORDER BY COALESCE(lastAssigned, '1970-01-01') ASC, u.userID ASC"
        " LIMIT 1",
        (category_id,))

    if tech is None:
        # AF-1: unassigned queue (status stays New), Managers notified.
        for m in query_all("SELECT userID FROM User"
                           " WHERE role = 'Manager' AND status = 'Active'"):
            notify(m["userID"],
                   f"Unassigned ticket #{ticket_id} needs routing",
                   f"'{title}' ({priority}) was submitted but no active "
                   "technician is available. It is waiting in the unassigned queue.")
        return None

    execute("UPDATE Ticket SET assignedToUserID = %s, status = 'Assigned'"
            " WHERE ticketID = %s", (tech["userID"], ticket_id))

    # System-triggered change; the submitter is recorded as the actor because
    # AuditLog.actorID is NOT NULL and the submission is what triggered it.
    log_action(session["user_id"], "Ticket", ticket_id, "Update",
               changes={"status": ("New", "Assigned"),
                        "assignedToUserID": (None, tech["userID"])},
               ip=request.remote_addr)

    notify(tech["userID"],
           f"Ticket #{ticket_id} assigned to you",
           f"'{title}' ({priority}) has been auto-assigned to you via "
           "category rotation.")
    return tech["name"]


# ---------------------------------------------------------------------------
# P1.3 — List (role-scoped, FR-5.1)
# ---------------------------------------------------------------------------

_LIST_CHIPS_STAFF = [("open", "All Open"), ("unassigned", "Unassigned"),
                     ("high", "High Priority"), ("closed", "Closed"), ("all", "All")]
_LIST_CHIPS_ENDUSER = [("open", "Open"), ("waiting", "Waiting on Me"),
                       ("resolved", "Resolved"), ("all", "All")]

_CHIP_CLAUSES = {
    "open":       " AND t.status IN ('New','Assigned','InProgress','WaitingOnUser')",
    "unassigned": " AND t.status = 'New' AND t.assignedToUserID IS NULL",
    "high":       " AND t.priority IN ('High','Critical') AND t.status <> 'Closed'",
    "waiting":    " AND t.status = 'WaitingOnUser'",
    "resolved":   " AND t.status = 'Resolved'",
    "closed":     " AND t.status = 'Closed'",
    "all":        "",
}


@bp.get("/tickets")
@login_required
def list_tickets():
    role, uid = session["role"], session["user_id"]
    view = request.args.get("view", "open")
    view = view if view in _CHIP_CLAUSES else "open"
    q = (request.args.get("q") or "").strip()

    sql, params = _TICKET_SELECT + " WHERE 1=1", []

    # FR-5.1 scoping happens in SQL, not in the template.
    if role == "EndUser":
        sql += " AND t.submittedByUserID = %s"
        params.append(uid)
        chips = _LIST_CHIPS_ENDUSER
    elif role == "Technician":
        sql += (" AND (t.assignedToUserID = %s"
                "      OR (t.status = 'New' AND t.assignedToUserID IS NULL))")
        params.append(uid)
        chips = _LIST_CHIPS_STAFF
    else:  # Manager / Administrator — everything
        chips = _LIST_CHIPS_STAFF

    sql += _CHIP_CLAUSES[view]
    if q:
        sql += " AND (t.title LIKE %s OR t.description LIKE %s)"
        params.extend([f"%{q}%"] * 2)

    rows = query_all(sql + " ORDER BY t.createdAt DESC LIMIT 100", tuple(params))
    return render_template("tickets/list.html",
                           tickets=[_shape(r) for r in rows],
                           chips=chips, view=view, q=q)


# ---------------------------------------------------------------------------
# P1.3 — Detail (role-scoped) + H2.1 linked resources
# ---------------------------------------------------------------------------

def _get_ticket_or_403(ticket_id):
    """Fetch a ticket and enforce FR-5.1 scoping. 404 if it doesn't exist,
    403 if it exists but this role/user may not see it — the P1.3 AC is a
    hard denial, mirroring roles_required's abort(403) convention."""
    t = query_one(_TICKET_SELECT + " WHERE t.ticketID = %s", (ticket_id,))
    if t is None:
        abort(404)
    role, uid = session["role"], session["user_id"]
    if role == "EndUser" and t["submittedByUserID"] != uid:
        abort(403)
    if role == "Technician" and t["assignedToUserID"] not in (uid, None):
        abort(403)  # assigned to someone else — not their queue, not theirs
    return t


def _render_detail(t, status_code=200):
    """Render the ticket detail page for a ticket row `t` (as returned by
    _get_ticket_or_403). Shared by view_ticket (200) and the change_status
    error path (400) — a 302 redirect cannot carry a 400 status, and the
    P1.1 create flow already sets the precedent of re-rendering with 400 on
    invalid input while a flashed message explains why."""
    ticket_id = t["ticketID"]

    linked = query_all(
        "SELECT r.resourceID, r.resourceTag, r.type, r.make, r.model,"
        "       r.status, tr.linkedAt"
        "  FROM TicketResource tr JOIN Resource r ON r.resourceID = tr.resourceID"
        " WHERE tr.ticketID = %s ORDER BY tr.linkedAt DESC", (ticket_id,))

    # The link picker is a search-as-you-type modal backed by
    # search_linkable() below — the full inventory is never shipped to the
    # browser (a 500-row <select> is neither usable nor scalable).

    history = query_all(
        "SELECT a.action, a.timestamp, CONCAT(u.firstName,' ',u.lastName) AS actor"
        "  FROM AuditLog a JOIN User u ON u.userID = a.actorID"
        " WHERE a.entityType = 'Ticket' AND a.entityID = %s"
        " ORDER BY a.timestamp DESC LIMIT 8", (ticket_id,))

    is_staff = session["role"] in STAFF

    # P2.1 (FR-2.4) — comment visibility is enforced HERE, in the query, not in
    # the template: an End User never receives Internal notes in their response
    # at all, so a view-source check finds zero internal content (the AC).
    comment_sql = (
        "SELECT tc.commentID, tc.commentType, tc.bodyText, tc.createdAt,"
        "       CONCAT(u.firstName, ' ', u.lastName) AS author"
        "  FROM TicketComment tc JOIN User u ON u.userID = tc.authorUserID"
        " WHERE tc.ticketID = %s")
    if not is_staff:
        comment_sql += " AND tc.commentType = 'Public'"
    comment_sql += " ORDER BY tc.createdAt ASC"
    comments = query_all(comment_sql, (ticket_id,))

    return render_template("tickets/detail.html", t=_shape(t),
                           linked=linked, history=history,
                           comments=comments, is_staff=is_staff)


# ---------------------------------------------------------------------------
# P2.1 — Comments: internal + public (FR-2.4)
# ---------------------------------------------------------------------------

@bp.post("/tickets/<int:ticket_id>/comments")
@login_required
def add_comment(ticket_id):
    """Add a comment to a ticket. Staff may post Internal (staff-only) or
    Public notes; everyone else is forced to Public — a non-staff user can
    never create an Internal note (FR-2.4). A Public comment notifies the
    submitter, unless they are the author (FR-2.5)."""
    t = _get_ticket_or_403(ticket_id)   # reuse P1.3 scoping (404 / 403)
    role, uid = session["role"], session["user_id"]

    body = (request.form.get("body") or "").strip()
    ctype = request.form.get("comment_type", "Public")
    # Internal is a staff-only privilege; anything else collapses to Public.
    if role not in STAFF or ctype not in ("Internal", "Public"):
        ctype = "Public"

    if not body:
        flash("Comment cannot be empty.", "error")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))
    if len(body) > 5000:
        flash("Comment is too long (5000 characters maximum).", "error")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    comment_id = execute(
        "INSERT INTO TicketComment (ticketID, authorUserID, commentType, bodyText)"
        " VALUES (%s, %s, %s, %s)", (ticket_id, uid, ctype, body))
    log_action(uid, "TicketComment", comment_id, "Create", ip=request.remote_addr)

    # FR-2.5: a Public comment reaches the submitter. Skip self-notification —
    # the submitter commenting on their own ticket doesn't email themselves.
    if ctype == "Public" and t["submittedByUserID"] != uid:
        notify(t["submittedByUserID"],
               f"New comment on ticket #{ticket_id}",
               f"A new public comment was added to '{t['title']}'.")

    label = "Internal note" if ctype == "Internal" else "Comment"
    flash(f"{label} added.", "success")
                           is_staff=session["role"] in STAFF,
                           next_states=sorted(TRANSITIONS[t["status"]]),
                           status_labels=STATUS_LABELS), status_code


@bp.get("/tickets/<int:ticket_id>")
@login_required
def view_ticket(ticket_id):
    t = _get_ticket_or_403(ticket_id)
    return _render_detail(t)


# ---------------------------------------------------------------------------
# P1.4 — Status transitions (FR-2.2, UC-02)
# ---------------------------------------------------------------------------

@bp.post("/tickets/<int:ticket_id>/status")
@roles_required(*STAFF)
def change_status(ticket_id):
    """Enforce the legal six-state lifecycle server-side. An illegal
    transition (or a resolve without a summary, or a forward move on an
    unassigned ticket a Manager can't claim) returns HTTP 400 and writes
    NOTHING (the UC-02 acceptance criterion). A legal transition updates the
    ticket in one parameterized UPDATE, audits the field diffs, and notifies
    the submitter (FR-2.5)."""
    t = _get_ticket_or_403(ticket_id)
    old = t["status"]
    new = (request.form.get("status") or "").strip()
    role, uid = session["role"], session["user_id"]

    # Guard 1 — legal edge (the core AC). Unknown or non-adjacent target rejected.
    if new not in STATUS_LABELS:
        flash("Unknown ticket status.", "error")
        return _render_detail(t, 400)
    if new not in TRANSITIONS[old]:
        flash(f"Cannot move a ticket from {STATUS_LABELS[old]} to "
              f"{STATUS_LABELS[new]}.", "error")
        return _render_detail(t, 400)

    # Build the column set + audit diff as we validate. Column fragments are
    # constant literals; every value is bound as a parameter (NFR-S4).
    sets = ["status = %s"]
    params = [new]
    changes = {"status": (old, new)}

    # Guard 2 — Assigned/InProgress/Resolved need an assignee. A Technician
    # auto-claims an unassigned ticket ("...unless claimed"); a Manager/Admin
    # must route it first.
    if new in _REQUIRES_ASSIGNEE and t["assignedToUserID"] is None:
        if role == "Technician":
            sets.append("assignedToUserID = %s")
            params.append(uid)
            changes["assignedToUserID"] = (None, uid)
        else:
            flash(f"Assign a technician before moving this ticket into "
                  f"{STATUS_LABELS[new]}.", "error")
            return _render_detail(t, 400)

    # Guard 3 — resolving requires a summary and stamps resolvedAt.
    if new == "Resolved":
        summary = (request.form.get("resolution_summary") or "").strip()
        if not summary:
            flash("A resolution summary is required to resolve a ticket.", "error")
            return _render_detail(t, 400)
        now = datetime.now()
        sets += ["resolvedAt = %s", "resolutionSummary = %s"]
        params += [now, summary]
        changes["resolvedAt"] = (t["resolvedAt"], now)
        changes["resolutionSummary"] = (t["resolutionSummary"], summary)

    # Reopen — Resolved→InProgress clears resolvedAt (the summary stays as history).
    if old == "Resolved" and new == "InProgress":
        sets.append("resolvedAt = %s")
        params.append(None)
        changes["resolvedAt"] = (t["resolvedAt"], None)

    params.append(ticket_id)
    execute("UPDATE Ticket SET " + ", ".join(sets) + " WHERE ticketID = %s",
            tuple(params))

    log_action(uid, "Ticket", ticket_id, "Update", changes=changes,
               ip=request.remote_addr)
    notify(t["submittedByUserID"],
           f"Ticket #{ticket_id} is now {STATUS_LABELS[new]}",
           f"'{t['title']}' moved from {STATUS_LABELS[old]} to "
           f"{STATUS_LABELS[new]}.")
    flash(f"Ticket #{ticket_id} is now {STATUS_LABELS[new]}.", "success")
    return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# H2.1 — Link / unlink resources (UC-03, FR-3.2)
# ---------------------------------------------------------------------------

@bp.get("/tickets/<int:ticket_id>/resources/search")
@roles_required(*STAFF)
def search_linkable(ticket_id):
    """JSON search behind the Associate Resources modal.

    Returns at most 20 not-yet-linked resources matching q across tag,
    make, model, serial, and location. Server-side search means the full
    inventory never reaches the browser, whatever its size. Empty q
    returns the first 20 by tag so the modal isn't blank on open.
    """
    _get_ticket_or_403(ticket_id)
    q = (request.args.get("q") or "").strip()

    sql = (
        "SELECT r.resourceID, r.resourceTag, r.type, r.make, r.model,"
        "       r.status, r.location,"
        "       CONCAT(u.firstName, ' ', u.lastName) AS assignedName"
        "  FROM Resource r LEFT JOIN User u ON u.userID = r.assignedUserID"
        " WHERE r.resourceID NOT IN"
        "       (SELECT resourceID FROM TicketResource WHERE ticketID = %s)")
    params = [ticket_id]
    if q:
        sql += ("   AND (r.resourceTag LIKE %s OR r.make LIKE %s"
                "        OR r.model LIKE %s OR r.serialNumber LIKE %s"
                "        OR r.location LIKE %s)")
        params.extend([f"%{q}%"] * 5)

    rows = query_all(sql + " ORDER BY r.resourceTag LIMIT 20", tuple(params))
    return {"results": [
        {"resourceID": r["resourceID"], "resourceTag": r["resourceTag"],
         "type": r["type"], "make": r["make"], "model": r["model"],
         "status": r["status"],
         "status_label": {"InUse": "In Use", "InStock": "In Stock",
                          "Disposed": "Disposed",
                          "LostMissing": "Lost / Missing"}[r["status"]],
         "location": r["location"],
         "assignedName": r["assignedName"]}
        for r in rows
    ]}


@bp.post("/tickets/<int:ticket_id>/resources")
@roles_required(*STAFF)
def link_resource(ticket_id):
    """Link one or more resources (checkbox multi-select from the modal)."""
    _get_ticket_or_403(ticket_id)

    ids = [int(v) for v in request.form.getlist("resource_ids") if v.isdigit()]
    if not ids:
        flash("Select at least one resource to link.", "error")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    linked_tags, retired_tags, dup_tags = [], [], []
    for resource_id in ids:
        resource = query_one("SELECT resourceID, resourceTag, status"
                             "  FROM Resource WHERE resourceID = %s",
                             (resource_id,))
        if resource is None:
            continue  # deleted between search and submit — skip silently
        try:
            execute("INSERT INTO TicketResource (ticketID, resourceID, linkedAt)"
                    " VALUES (%s, %s, NOW())", (ticket_id, resource_id))
        except IntegrityError:
            # AF-2: composite PK (ticketID, resourceID) already exists.
            # The DB is the source of truth — a pre-check would race.
            dup_tags.append(resource["resourceTag"])
            continue

        log_action(session["user_id"], "TicketResource", ticket_id, "Link",
                   changes={"resourceID": (None, resource_id)},
                   ip=request.remote_addr)
        linked_tags.append(resource["resourceTag"])
        if resource["status"] in ("Disposed", "LostMissing"):
            retired_tags.append(resource["resourceTag"])

    if linked_tags:
        flash(f"Linked {', '.join(linked_tags)} to this ticket.", "success")
    if retired_tags:
        # EX-2: linking a retired/lost resource is legal (lost-hardware
        # incidents are exactly this case) but must be conspicuous.
        flash(f"Note: {', '.join(retired_tags)} "
              f"{'is' if len(retired_tags) == 1 else 'are'} marked "
              "Disposed or Lost/Missing.", "warning")
    if dup_tags:
        flash(f"Already linked: {', '.join(dup_tags)}.", "info")
    return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))


@bp.post("/tickets/<int:ticket_id>/resources/unlink")
@roles_required(*STAFF)
def unlink_resource(ticket_id):
    _get_ticket_or_403(ticket_id)

    raw = request.form.get("resource_id", "")
    resource_id = int(raw) if raw.isdigit() else 0

    execute("DELETE FROM TicketResource"
            " WHERE ticketID = %s AND resourceID = %s",
            (ticket_id, resource_id))
    # execute() returns lastrowid for INSERTs; for DELETE we verify by
    # re-checking existence rather than relying on the return value.
    still_there = query_one("SELECT 1 AS x FROM TicketResource"
                            " WHERE ticketID = %s AND resourceID = %s",
                            (ticket_id, resource_id))
    if still_there:
        flash("Could not unlink that resource.", "error")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    log_action(session["user_id"], "TicketResource", ticket_id, "Unlink",
               changes={"resourceID": (resource_id, None)},
               ip=request.remote_addr)
    flash("Resource unlinked from this ticket.", "success")
    return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shape(r):
    """Attach presentation data so templates stay logic-free (same convention
    as resources._shape / main._shape)."""
    r = dict(r)
    r["status_label"] = STATUS_LABELS[r["status"]]
    r["status_badge"] = STATUS_BADGES[r["status"]]
    r["priority_badge"] = PRIORITY_BADGES[r["priority"]]
    r["unassigned"] = r["assignedToUserID"] is None
    r["age"] = _age(r["createdAt"])
    return r


def _age(dt):
    mins = int((datetime.now() - dt).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min{'s' if mins != 1 else ''} ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"

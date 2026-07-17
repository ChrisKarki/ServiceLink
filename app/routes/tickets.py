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

Deferred (visible stubs in the detail template, not silent gaps):
  P1.4  status transitions, resolvedAt, resolution summary
  P2.x  comments, attachments

All mutations go through services.audit.log_action — no local audit INSERTs
(C0.1 contract). Every user-supplied value is bound as a parameter (NFR-S4).
"""

from datetime import datetime, timedelta

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, session, url_for)
from mysql.connector.errors import IntegrityError

from ..db import execute, query_all, query_one
from ..services.audit import diff_fields, log_action
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

    # Right-panel filters (Freshservice-style). Every value is validated
    # against a whitelist or bound as a parameter (NFR-S4).
    f_status = request.args.get("status") or None
    f_status = f_status if f_status in STATUS_LABELS else None
    f_priority = request.args.get("priority") or None
    f_priority = f_priority if f_priority in PRIORITIES else None
    f_category = request.args.get("category") or None
    f_category = int(f_category) if f_category and f_category.isdigit() else None
    f_tech = request.args.get("tech") or None
    f_tech = int(f_tech) if f_tech and f_tech.isdigit() else None
    f_requester = (request.args.get("requester") or "").strip()

    sql, params = _TICKET_SELECT + " WHERE 1=1", []

    # FR-5.1 scoping happens in SQL, not in the template.
    if role == "EndUser":
        sql += " AND t.submittedByUserID = %s"
        params.append(uid)
        chips = _LIST_CHIPS_ENDUSER
        f_tech = f_requester = None  # staff-only filters
    elif role == "Technician":
        sql += (" AND (t.assignedToUserID = %s"
                "      OR (t.status = 'New' AND t.assignedToUserID IS NULL))")
        params.append(uid)
        chips = _LIST_CHIPS_STAFF
    else:  # Manager / Administrator — everything
        chips = _LIST_CHIPS_STAFF

    sql += _CHIP_CLAUSES[view]
    if f_status:
        sql += " AND t.status = %s"
        params.append(f_status)
    if f_priority:
        sql += " AND t.priority = %s"
        params.append(f_priority)
    if f_category:
        sql += " AND t.categoryID = %s"
        params.append(f_category)
    if f_tech:
        sql += " AND t.assignedToUserID = %s"
        params.append(f_tech)
    if f_requester:
        sql += (" AND (CONCAT(s.firstName, ' ', s.lastName) LIKE %s"
                "      OR s.email LIKE %s)")
        params.extend([f"%{f_requester}%"] * 2)
    if q:
        sql += " AND (t.title LIKE %s OR t.description LIKE %s)"
        params.extend([f"%{q}%"] * 2)

    rows = query_all(sql + " ORDER BY t.createdAt DESC LIMIT 100", tuple(params))

    return render_template(
        "tickets/list.html",
        tickets=[_shape(r) for r in rows],
        chips=chips, view=view,
        filters={"q": q, "status": f_status, "priority": f_priority,
                 "category": f_category, "tech": f_tech,
                 "requester": f_requester},
        categories=query_all("SELECT categoryID, name FROM Category"
                             " WHERE isActive = TRUE ORDER BY name"),
        technicians=_active_technicians() if role != "EndUser" else [],
        priorities=PRIORITIES, status_labels=STATUS_LABELS,
    )


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


@bp.get("/tickets/<int:ticket_id>")
@login_required
def view_ticket(ticket_id):
    t = _get_ticket_or_403(ticket_id)
    is_staff = session["role"] in STAFF

    linked = query_all(
        "SELECT r.resourceID, r.resourceTag, r.type, r.make, r.model,"
        "       r.status, tr.linkedAt"
        "  FROM TicketResource tr JOIN Resource r ON r.resourceID = tr.resourceID"
        " WHERE tr.ticketID = %s ORDER BY tr.linkedAt DESC", (ticket_id,))

    # Conversation (FR-2.4): internal notes are staff-only — filtered in
    # SQL so they never reach an End User's page source.
    # Schema (schema.sql §TicketComment): bodyText TEXT and
    # commentType ENUM('Internal','Public') — aliased here so the template
    # keeps working with .body / .isInternal.
    c_sql = ("SELECT c.commentID, c.bodyText AS body,"
             "       (c.commentType = 'Internal') AS isInternal, c.createdAt,"
             "       CONCAT(u.firstName, ' ', u.lastName) AS author,"
             "       UPPER(CONCAT(LEFT(u.firstName,1), LEFT(u.lastName,1))) AS initials,"
             "       u.role AS authorRole"
             "  FROM TicketComment c JOIN User u ON u.userID = c.authorUserID"
             " WHERE c.ticketID = %s")
    if not is_staff:
        c_sql += " AND c.commentType = 'Public'"
    comments = query_all(c_sql + " ORDER BY c.createdAt ASC", (ticket_id,))

    history = query_all(
        "SELECT a.action, a.timestamp, CONCAT(u.firstName,' ',u.lastName) AS actor"
        "  FROM AuditLog a JOIN User u ON u.userID = a.actorID"
        " WHERE a.entityType IN ('Ticket', 'TicketResource', 'TicketComment')"
        "   AND a.entityID = %s"
        " ORDER BY a.timestamp DESC LIMIT 20", (ticket_id,))

    return render_template(
        "tickets/detail.html", t=_shape(t),
        linked=linked, comments=comments, history=history,
        sla=_sla_info(t), is_staff=is_staff,
        technicians=_active_technicians() if is_staff else [],
        categories=query_all("SELECT categoryID, name FROM Category"
                             " WHERE isActive = TRUE ORDER BY name"),
        priorities=PRIORITIES, statuses=list(STATUS_LABELS),
        status_labels=STATUS_LABELS)


def _sla_info(t):
    """Resolution-due data from SLAPolicy (FR-2.5). Returns None if no
    policy row exists for the priority (seed gap) — the template then
    shows 'No SLA policy' instead of guessing."""
    policy = query_one("SELECT responseTargetMins, resolutionTargetMins"
                       "  FROM SLAPolicy WHERE priority = %s", (t["priority"],))
    if policy is None:
        return None
    due = t["createdAt"] + timedelta(minutes=policy["resolutionTargetMins"])
    info = {"due": due, "due_label": due.strftime("%a, %b %d %Y, %I:%M %p"),
            "breached": bool(t["slaBreached"])}
    if t["status"] in ("Resolved", "Closed"):
        info["state"] = "closed"
        info["timer"] = "Breached" if t["slaBreached"] else "Met"
    else:
        remaining = due - datetime.now()
        if remaining.total_seconds() <= 0:
            info["state"] = "overdue"
            info["timer"] = _span(-remaining) + " overdue"
        else:
            info["state"] = "ok"
            info["timer"] = _span(remaining) + " left"
    return info


def _span(td):
    mins = int(td.total_seconds() // 60)
    d, h, m = mins // 1440, (mins % 1440) // 60, mins % 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _active_technicians():
    return query_all(
        "SELECT userID, CONCAT(firstName, ' ', lastName) AS name FROM User"
        " WHERE role = 'Technician' AND status = 'Active' ORDER BY firstName")


# ---------------------------------------------------------------------------
# Properties panel (status / priority / assignee / category) — staff only
# ---------------------------------------------------------------------------

_PROP_FIELDS = ["status", "priority", "assignedToUserID", "categoryID"]


@bp.post("/tickets/<int:ticket_id>/properties")
@roles_required(*STAFF)
def update_properties(ticket_id):
    before = _get_ticket_or_403(ticket_id)

    status = request.form.get("status", "")
    priority = request.form.get("priority", "")
    assignee_raw = request.form.get("assignedToUserID", "")
    category_raw = request.form.get("categoryID", "")

    if status not in STATUS_LABELS or priority not in PRIORITIES \
            or not category_raw.isdigit():
        flash("Invalid property values.", "error")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))
    assignee = int(assignee_raw) if assignee_raw.isdigit() else None
    if assignee and query_one("SELECT 1 AS x FROM User WHERE userID = %s"
                              "   AND role = 'Technician' AND status = 'Active'",
                              (assignee,)) is None:
        flash("Assignee must be an active technician.", "error")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    after = {"status": status, "priority": priority,
             "assignedToUserID": assignee, "categoryID": int(category_raw)}
    changes = diff_fields(before, after, _PROP_FIELDS)
    if not changes:
        flash("No changes to save.", "info")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    # resolvedAt bookkeeping (§4.2 derived-fields rules): stamp on entry to
    # Resolved, clear if the ticket is reopened to an active state.
    resolved_at = before["resolvedAt"]
    if status == "Resolved" and before["status"] != "Resolved":
        resolved_at = datetime.now()
    elif status in ("New", "Assigned", "InProgress", "WaitingOnUser") \
            and before["status"] in ("Resolved", "Closed"):
        resolved_at = None

    execute("UPDATE Ticket SET status=%s, priority=%s, assignedToUserID=%s,"
            "  categoryID=%s, resolvedAt=%s WHERE ticketID=%s",
            (status, priority, assignee, int(category_raw), resolved_at,
             ticket_id))

    log_action(session["user_id"], "Ticket", ticket_id, "Update",
               changes=changes, ip=request.remote_addr)

    if "assignedToUserID" in changes and assignee:
        notify(assignee, f"Ticket #{ticket_id} assigned to you",
               f"'{before['title']}' was assigned to you by "
               f"{session['name']}.")
    flash("Ticket updated.", "success")
    return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# Conversation (FR-2.4) — public replies + staff-only internal notes
# ---------------------------------------------------------------------------

@bp.post("/tickets/<int:ticket_id>/comments")
@login_required
def add_comment(ticket_id):
    t = _get_ticket_or_403(ticket_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Comment cannot be empty.", "error")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    # Only staff can mark a note internal — an End User posting
    # is_internal=1 by hand-crafting the form is silently ignored.
    is_internal = session["role"] in STAFF and request.form.get("is_internal") == "1"

    comment_id = execute(
        "INSERT INTO TicketComment (ticketID, authorUserID, commentType,"
        "  bodyText, createdAt) VALUES (%s, %s, %s, %s, NOW())",
        (ticket_id, session["user_id"],
         "Internal" if is_internal else "Public", body))

    log_action(session["user_id"], "TicketComment", ticket_id, "Create",
               changes={"commentID": (None, comment_id)},
               ip=request.remote_addr)

    # Notify the other side of the conversation (never for internal notes).
    if not is_internal:
        if session["role"] in STAFF and t["submittedByUserID"] != session["user_id"]:
            notify(t["submittedByUserID"], f"New reply on ticket #{ticket_id}",
                   f"{session['name']} replied to '{t['title']}'.")
        elif session["role"] == "EndUser" and t["assignedToUserID"]:
            notify(t["assignedToUserID"], f"New reply on ticket #{ticket_id}",
                   f"{session['name']} replied to '{t['title']}'.")

    flash("Internal note added." if is_internal else "Reply added.", "success")
    return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# Resolution notes — staff only
# ---------------------------------------------------------------------------

@bp.post("/tickets/<int:ticket_id>/resolution")
@roles_required(*STAFF)
def save_resolution(ticket_id):
    before = _get_ticket_or_403(ticket_id)
    summary = (request.form.get("resolutionSummary") or "").strip() or None

    if summary == before["resolutionSummary"]:
        flash("No changes to save.", "info")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    execute("UPDATE Ticket SET resolutionSummary = %s WHERE ticketID = %s",
            (summary, ticket_id))
    log_action(session["user_id"], "Ticket", ticket_id, "Update",
               changes={"resolutionSummary": (before["resolutionSummary"],
                                              summary)},
               ip=request.remote_addr)
    flash("Resolution notes saved.", "success")
    return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))


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

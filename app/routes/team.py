"""Team management (C3.2) — roster, workload, and technician groups.

This sprint the page gains write paths (Manager/Administrator only):

    Groups        create / rename / activate-deactivate TechGroup rows
    Membership    assign staff (Technician or higher) to groups from the
                  roster — a member may sit in several groups
    Routing       map groups onto ticket categories (CategoryGroup); the
                  P1.2 round-robin in tickets.py consumes this mapping:
                  category -> round-robin over its groups -> round-robin
                  over that group's active Technicians

Conventions unchanged: parameters bound (NFR-S4), every mutation through
services.audit.log_action (FR-6.2), denial is abort(403) via
roles_required. Group CRUD audits as entityType 'TechGroup' (added to the
DB ENUM in migration 007 and to audit.ENTITY_TYPES in the same change
set); membership audits against the User row and routing against the
Category row, so their history shows up where people look for it.

Workload aggregation keeps the schema-drift defence from reports.py:
SELECT * over Ticket, resolve the assignee key in Python.
"""

from flask import (Blueprint, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from mysql.connector.errors import IntegrityError

from ..db import execute, query_all, query_one
from ..services.audit import log_action
from .auth import roles_required

bp = Blueprint("team", __name__, url_prefix="/team")

STAFF_ROLES = ("Technician", "Manager", "Administrator")
OPEN_STATUSES = {"New", "Assigned", "InProgress", "WaitingOnUser"}
ASSIGNEE_KEYS = ("assignedToUserID", "assignedUserID", "technicianID",
                 "assigneeID", "assignedTechID", "assigned_user_id")


def _assignee(ticket):
    for k in ASSIGNEE_KEYS:
        if k in ticket and ticket[k] is not None:
            return ticket[k]
    return None


def _wants_json():
    """The redesigned team page auto-saves via fetch(); those requests
    carry X-Requested-With so the same endpoints can answer JSON while
    plain form POSTs (Katie's TC paths, no-JS fallback) keep the
    flash-and-redirect behaviour."""
    return request.headers.get("X-Requested-With") == "fetch"


# ---------------------------------------------------------------------------
# Roster + groups + routing (one page)
# ---------------------------------------------------------------------------

@bp.get("/")
@roles_required("Manager", "Administrator")
def index():
    members = query_all(
        "SELECT userID, firstName, lastName, email, role, status, lastLoginAt"
        "  FROM User"
        " WHERE role IN ('Technician', 'Manager', 'Administrator')"
        " ORDER BY FIELD(role, 'Administrator', 'Manager', 'Technician'),"
        "          lastName, firstName", ())

    tickets = query_all("SELECT * FROM Ticket", ())

    load = {}
    for t in tickets:
        uid = _assignee(t)
        if uid is None:
            continue
        row = load.setdefault(uid, {"open": 0, "in_progress": 0,
                                    "resolved": 0})
        status = t.get("status")
        if status in OPEN_STATUSES:
            row["open"] += 1
            if status == "InProgress":
                row["in_progress"] += 1
        elif status in ("Resolved", "Closed"):
            row["resolved"] += 1

    max_open = max((v["open"] for v in load.values()), default=0) or 1
    for m in members:
        stats = load.get(m["userID"], {"open": 0, "in_progress": 0,
                                       "resolved": 0})
        m.update(stats)
        m["load_pct"] = round(stats["open"] * 100 / max_open)

    unassigned_open = sum(
        1 for t in tickets
        if _assignee(t) is None and t.get("status") in OPEN_STATUSES)

    # -- groups, membership, and category routing ----------------------
    groups = query_all(
        "SELECT g.groupID, g.name, g.isActive,"
        "       COUNT(DISTINCT m.userID) AS memberCount"
        "  FROM TechGroup g"
        "  LEFT JOIN TechGroupMember m ON m.groupID = g.groupID"
        " GROUP BY g.groupID, g.name, g.isActive"
        " ORDER BY g.isActive DESC, g.name", ())

    memberships = query_all(
        "SELECT groupID, userID FROM TechGroupMember", ())
    member_groups = {}          # userID -> [groupID, ...]
    for r in memberships:
        member_groups.setdefault(r["userID"], []).append(r["groupID"])
    for m in members:
        m["groupIDs"] = member_groups.get(m["userID"], [])

    categories = query_all(
        "SELECT categoryID, name, isActive FROM Category"
        " ORDER BY isActive DESC, name", ())
    routing = query_all(
        "SELECT categoryID, groupID FROM CategoryGroup", ())
    category_groups = {}        # categoryID -> [groupID, ...]
    for r in routing:
        category_groups.setdefault(r["categoryID"], []).append(r["groupID"])
    for c in categories:
        c["groupIDs"] = category_groups.get(c["categoryID"], [])

    return render_template("team/index.html", members=members,
                           unassigned_open=unassigned_open,
                           groups=groups, categories=categories)


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------

def _get_group(group_id):
    row = query_one("SELECT * FROM TechGroup WHERE groupID = %s", (group_id,))
    if row is None:
        abort(404)
    return row


@bp.post("/groups/new")
@roles_required("Manager", "Administrator")
def create_group():
    name = (request.form.get("name") or "").strip()
    if not name or len(name) > 80:
        flash("Group name is required (max 80 characters).", "error")
        return redirect(url_for("team.index"))
    try:
        group_id = execute(
            "INSERT INTO TechGroup (name, isActive) VALUES (%s, TRUE)",
            (name,))
    except IntegrityError:
        flash(f"A group named '{name}' already exists.", "error")
        return redirect(url_for("team.index"))
    log_action(session["user_id"], "TechGroup", group_id, "Create",
               changes={"name": (None, name)}, ip=request.remote_addr)
    flash(f"Group '{name}' created. Add members below, then map it onto "
          "one or more categories.", "success")
    return redirect(url_for("team.index"))


@bp.post("/groups/<int:group_id>/rename")
@roles_required("Manager", "Administrator")
def rename_group(group_id):
    before = _get_group(group_id)
    name = (request.form.get("name") or "").strip()
    if not name or len(name) > 80:
        if _wants_json():
            return jsonify(ok=False, error="Group name is required "
                           "(max 80 characters)."), 400
        flash("Group name is required (max 80 characters).", "error")
        return redirect(url_for("team.index"))
    if name == before["name"]:
        if _wants_json():
            return jsonify(ok=True, unchanged=True, name=name)
        flash("No changes to save.", "info")
        return redirect(url_for("team.index"))
    try:
        execute("UPDATE TechGroup SET name = %s WHERE groupID = %s",
                (name, group_id))
    except IntegrityError:
        if _wants_json():
            return jsonify(ok=False,
                           error=f"A group named '{name}' already "
                           "exists."), 409
        flash(f"A group named '{name}' already exists.", "error")
        return redirect(url_for("team.index"))
    log_action(session["user_id"], "TechGroup", group_id, "Update",
               changes={"name": (before["name"], name)},
               ip=request.remote_addr)
    if _wants_json():
        return jsonify(ok=True, name=name)
    flash("Group renamed.", "success")
    return redirect(url_for("team.index"))


@bp.post("/groups/<int:group_id>/toggle")
@roles_required("Manager", "Administrator")
def toggle_group(group_id):
    """Soft activate/deactivate — mirrors Category. Hard delete is not
    offered: Ticket.assignedGroupID references this row, and a deactivated
    group simply drops out of the auto-assignment rotation."""
    before = _get_group(group_id)
    new_active = not bool(before["isActive"])
    execute("UPDATE TechGroup SET isActive = %s WHERE groupID = %s",
            (new_active, group_id))
    log_action(session["user_id"], "TechGroup", group_id, "Update",
               changes={"isActive": (bool(before["isActive"]), new_active)},
               ip=request.remote_addr)
    flash(f"Group '{before['name']}' "
          f"{'activated' if new_active else 'deactivated'}."
          + ("" if new_active else " It no longer receives auto-assigned "
             "tickets; existing tickets keep it."),
          "success")
    return redirect(url_for("team.index"))


# ---------------------------------------------------------------------------
# Membership — set a staff member's groups from the roster row
# ---------------------------------------------------------------------------

@bp.post("/members/<int:user_id>/groups")
@roles_required("Manager", "Administrator")
def set_member_groups(user_id):
    user = query_one(
        "SELECT userID, firstName, lastName, role FROM User"
        " WHERE userID = %s", (user_id,))
    if user is None:
        abort(404)
    if user["role"] not in STAFF_ROLES:
        if _wants_json():
            return jsonify(ok=False, error="Only Technicians, Managers, "
                           "and Administrators can belong to groups."), 400
        flash("Only Technicians, Managers, and Administrators can belong "
              "to groups.", "error")
        return redirect(url_for("team.index"))

    wanted = {int(v) for v in request.form.getlist("group_ids")
              if v.isdigit()}
    valid = {g["groupID"]: g["name"] for g in
             query_all("SELECT groupID, name FROM TechGroup", ())}
    wanted &= set(valid)

    current = {r["groupID"] for r in query_all(
        "SELECT groupID FROM TechGroupMember WHERE userID = %s",
        (user_id,))}

    def _payload():
        return jsonify(ok=True,
                       groupIDs=sorted(wanted),
                       names=sorted(valid[g] for g in wanted))

    if wanted == current:
        if _wants_json():
            return _payload()
        flash("No changes to save.", "info")
        return redirect(url_for("team.index"))

    for gid in current - wanted:
        execute("DELETE FROM TechGroupMember"
                " WHERE groupID = %s AND userID = %s", (gid, user_id))
    for gid in wanted - current:
        execute("INSERT INTO TechGroupMember (groupID, userID)"
                " VALUES (%s, %s)", (gid, user_id))

    def _names(ids):
        return ", ".join(sorted(valid[g] for g in ids)) or None

    log_action(session["user_id"], "User", user_id, "Update",
               changes={"groups": (_names(current), _names(wanted))},
               ip=request.remote_addr)
    if _wants_json():
        return _payload()
    flash(f"Groups updated for {user['firstName']} {user['lastName']}.",
          "success")
    return redirect(url_for("team.index"))


# ---------------------------------------------------------------------------
# Routing — map groups onto a category
# ---------------------------------------------------------------------------

@bp.post("/categories/<int:category_id>/groups")
@roles_required("Manager", "Administrator")
def set_category_groups(category_id):
    category = query_one(
        "SELECT categoryID, name FROM Category WHERE categoryID = %s",
        (category_id,))
    if category is None:
        abort(404)

    wanted = {int(v) for v in request.form.getlist("group_ids")
              if v.isdigit()}
    valid = {g["groupID"]: g["name"] for g in
             query_all("SELECT groupID, name FROM TechGroup", ())}
    wanted &= set(valid)

    current = {r["groupID"] for r in query_all(
        "SELECT groupID FROM CategoryGroup WHERE categoryID = %s",
        (category_id,))}

    def _payload():
        return jsonify(ok=True,
                       groupIDs=sorted(wanted),
                       names=sorted(valid[g] for g in wanted))

    if wanted == current:
        if _wants_json():
            return _payload()
        flash("No changes to save.", "info")
        return redirect(url_for("team.index"))

    for gid in current - wanted:
        execute("DELETE FROM CategoryGroup"
                " WHERE categoryID = %s AND groupID = %s",
                (category_id, gid))
    for gid in wanted - current:
        execute("INSERT INTO CategoryGroup (categoryID, groupID)"
                " VALUES (%s, %s)", (category_id, gid))

    def _names(ids):
        return ", ".join(sorted(valid[g] for g in ids)) or None

    log_action(session["user_id"], "Category", category_id, "Update",
               changes={"groups": (_names(current), _names(wanted))},
               ip=request.remote_addr)
    if _wants_json():
        return _payload()
    flash(f"Routing updated for category '{category['name']}'. New tickets "
          "in it round-robin across the selected groups.", "success")
    return redirect(url_for("team.index"))

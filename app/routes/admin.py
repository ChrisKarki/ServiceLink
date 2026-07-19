"""Administration console — FR-6.1 (C-cards).

Three panels, Administrator-only (enforced by @roles_required on every
route, never by hiding nav links):

    Users       approve / suspend / reactivate / role change
                (activates the FR-1.2 PendingApproval flow — the
                 acceptance test is approving prabh.hans through the UI
                 and having that account log in)
    Categories  create / rename / activate-deactivate
                (soft-deactivate only: Ticket.categoryID is an FK, and
                 round-robin auto-assignment keys off active categories)
    SLA targets edit responseTargetMins / resolutionTargetMins per
                priority (FR-2.5 escalation timers read these live)

Endpoints
    GET   /admin/                       -> redirect to /admin/users
    GET   /admin/users                  list + tab/role filters + search
    POST  /admin/users/<id>/approve
    POST  /admin/users/<id>/suspend
    POST  /admin/users/<id>/reactivate
    POST  /admin/users/<id>/role
    GET   /admin/categories
    POST  /admin/categories/new
    POST  /admin/categories/<id>/rename
    POST  /admin/categories/<id>/toggle
    GET   /admin/sla
    POST  /admin/sla/<priority>

Conventions (locked project-wide):
    - every user-supplied value is bound as a parameter (NFR-S4)
    - every mutation goes through services.audit.log_action (FR-6.2);
      updates carry field-level old/new via diff_fields
    - denial is abort(403) via roles_required, matching the tickets and
      resources blueprints

Lockout guards (defensive, not in the FR text but demo-saving):
    - an administrator cannot suspend themselves or change their own role
    - the last Active Administrator cannot be suspended or demoted
"""

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, session, url_for)
from mysql.connector.errors import IntegrityError

from ..db import execute, query_all, query_one
from ..services.audit import diff_fields, log_action
from ..services import notify
from .auth import roles_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

ROLES = ("EndUser", "Technician", "Manager", "Administrator")
STATUSES = ("PendingApproval", "Active", "Suspended")
PRIORITIES = ("Critical", "High", "Medium", "Low")

STATUS_LABELS = {"PendingApproval": "Pending Approval",
                 "Active": "Active",
                 "Suspended": "Suspended"}

# Badge styling per user status, reusing shared classes from main.css.
_WARN = "border: 1px solid var(--warning-color); color: var(--warning-color);"
_DANGER = "border: 1px solid var(--danger-color); color: var(--danger-color);"
STATUS_BADGES = {
    "PendingApproval": {"cls": "badge", "style": _WARN},
    "Active":          {"cls": "badge badge-status-resolved", "style": ""},
    "Suspended":       {"cls": "badge", "style": _DANGER},
}

_USER_SELECT = (
    "SELECT userID, email, firstName, lastName, role, status,"
    "       mfaEnabled, createdAt, lastLoginAt"
    "  FROM User")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user(user_id):
    row = query_one(_USER_SELECT + " WHERE userID = %s", (user_id,))
    if row is None:
        abort(404)
    return row


def _active_admin_count():
    row = query_one(
        "SELECT COUNT(*) AS n FROM User"
        " WHERE role = 'Administrator' AND status = 'Active'", ())
    return row["n"] if row else 0


def _is_last_active_admin(user):
    return (user["role"] == "Administrator"
            and user["status"] == "Active"
            and _active_admin_count() <= 1)


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------

@bp.get("/")
@roles_required("Administrator")
def index():
    return redirect(url_for("admin.list_users"))


# ===========================================================================
# Panel 1 — Users (FR-1.2 approval flow + FR-6.1 role management)
# ===========================================================================

@bp.get("/users")
@roles_required("Administrator")
def list_users():
    tab = request.args.get("tab", "pending")
    tab = tab if tab in ("pending", "active", "suspended", "all") else "pending"
    f_role = request.args.get("role") or None
    f_role = f_role if f_role in ROLES else None
    q = (request.args.get("q") or "").strip()

    sql, params = _USER_SELECT + " WHERE 1=1", []
    tab_clause = {"pending": " AND status = 'PendingApproval'",
                  "active": " AND status = 'Active'",
                  "suspended": " AND status = 'Suspended'",
                  "all": ""}
    sql += tab_clause[tab]
    if f_role:
        sql += " AND role = %s"
        params.append(f_role)
    if q:
        sql += (" AND (email LIKE %s OR firstName LIKE %s OR lastName LIKE %s"
                " OR CONCAT(firstName, ' ', lastName) LIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like, like])
    sql += " ORDER BY (status = 'PendingApproval') DESC, lastName, firstName"

    users = query_all(sql, tuple(params))

    counts_row = query_one(
        "SELECT SUM(status = 'PendingApproval') AS pending,"
        "       SUM(status = 'Active')          AS active,"
        "       SUM(status = 'Suspended')       AS suspended,"
        "       COUNT(*)                        AS total"
        "  FROM User", ())
    counts = {k: int(counts_row[k] or 0)
              for k in ("pending", "active", "suspended", "total")}

    return render_template("admin/users.html", users=users, tab=tab,
                           f_role=f_role, q=q, counts=counts, roles=ROLES,
                           status_labels=STATUS_LABELS,
                           status_badges=STATUS_BADGES,
                           current_user_id=session["user_id"])


@bp.post("/users/<int:user_id>/approve")
@roles_required("Administrator")
def approve_user(user_id):
    user = _get_user(user_id)
    if user["status"] != "PendingApproval":
        flash("Only accounts pending approval can be approved.", "error")
        return redirect(url_for("admin.list_users"))

    execute("UPDATE User SET status = 'Active' WHERE userID = %s", (user_id,))
    log_action(session["user_id"], "User", user_id, "Update",
               changes={"status": (user["status"], "Active")},
               ip=request.remote_addr)
    try:
        notify.send(user_id, "Your ServiceLink account has been approved",
                    "An administrator approved your account. You can now "
                    "sign in with your registered email address.")
    except Exception:
        pass  # the notification stub must never block an approval
    flash(f"{user['firstName']} {user['lastName']} approved — the account "
          "can now sign in.", "success")
    return redirect(url_for("admin.list_users"))


@bp.post("/users/<int:user_id>/suspend")
@roles_required("Administrator")
def suspend_user(user_id):
    user = _get_user(user_id)
    if user_id == session["user_id"]:
        flash("You cannot suspend your own account.", "error")
        return redirect(url_for("admin.list_users", tab="active"))
    if user["status"] == "Suspended":
        flash("That account is already suspended.", "info")
        return redirect(url_for("admin.list_users", tab="suspended"))
    if _is_last_active_admin(user):
        flash("Cannot suspend the last active administrator.", "error")
        return redirect(url_for("admin.list_users", tab="active"))

    execute("UPDATE User SET status = 'Suspended' WHERE userID = %s",
            (user_id,))
    log_action(session["user_id"], "User", user_id, "Update",
               changes={"status": (user["status"], "Suspended")},
               ip=request.remote_addr)
    flash(f"{user['firstName']} {user['lastName']} suspended. Existing "
          "sessions are rejected at the next request.", "success")
    return redirect(url_for("admin.list_users", tab="active"))


@bp.post("/users/<int:user_id>/reactivate")
@roles_required("Administrator")
def reactivate_user(user_id):
    user = _get_user(user_id)
    if user["status"] != "Suspended":
        flash("Only suspended accounts can be reactivated.", "error")
        return redirect(url_for("admin.list_users"))

    execute("UPDATE User SET status = 'Active' WHERE userID = %s", (user_id,))
    log_action(session["user_id"], "User", user_id, "Update",
               changes={"status": (user["status"], "Active")},
               ip=request.remote_addr)
    flash(f"{user['firstName']} {user['lastName']} reactivated.", "success")
    return redirect(url_for("admin.list_users", tab="suspended"))


@bp.post("/users/<int:user_id>/role")
@roles_required("Administrator")
def change_role(user_id):
    user = _get_user(user_id)
    new_role = request.form.get("role")
    if new_role not in ROLES:
        flash("Invalid role.", "error")
        return redirect(url_for("admin.list_users"))
    if user_id == session["user_id"]:
        flash("You cannot change your own role.", "error")
        return redirect(url_for("admin.list_users"))
    if new_role == user["role"]:
        flash("No change — the account already has that role.", "info")
        return redirect(url_for("admin.list_users"))
    if user["role"] == "Administrator" and _is_last_active_admin(user):
        flash("Cannot demote the last active administrator.", "error")
        return redirect(url_for("admin.list_users"))

    execute("UPDATE User SET role = %s WHERE userID = %s",
            (new_role, user_id))
    log_action(session["user_id"], "User", user_id, "Update",
               changes={"role": (user["role"], new_role)},
               ip=request.remote_addr)
    flash(f"{user['firstName']} {user['lastName']} is now {new_role}.",
          "success")
    return redirect(url_for("admin.list_users"))


# ===========================================================================
# Panel 2 — Categories (FR-6.1: admins configure ticket categories)
# ===========================================================================

@bp.get("/categories")
@roles_required("Administrator")
def list_categories():
    categories = query_all(
        "SELECT c.categoryID, c.name, c.isActive,"
        "       COUNT(t.ticketID) AS ticketCount"
        "  FROM Category c"
        "  LEFT JOIN Ticket t ON t.categoryID = c.categoryID"
        " GROUP BY c.categoryID, c.name, c.isActive"
        " ORDER BY c.isActive DESC, c.name", ())
    return render_template("admin/categories.html", categories=categories)


@bp.post("/categories/new")
@roles_required("Administrator")
def create_category():
    name = (request.form.get("name") or "").strip()
    if not name or len(name) > 80:
        flash("Category name is required (max 80 characters).", "error")
        return redirect(url_for("admin.list_categories"))
    try:
        category_id = execute(
            "INSERT INTO Category (name, isActive) VALUES (%s, TRUE)",
            (name,))
    except IntegrityError:
        flash(f"A category named '{name}' already exists.", "error")
        return redirect(url_for("admin.list_categories"))
    log_action(session["user_id"], "Category", category_id, "Create",
               changes={"name": (None, name)}, ip=request.remote_addr)
    flash(f"Category '{name}' created.", "success")
    return redirect(url_for("admin.list_categories"))


@bp.post("/categories/<int:category_id>/rename")
@roles_required("Administrator")
def rename_category(category_id):
    before = query_one("SELECT * FROM Category WHERE categoryID = %s",
                       (category_id,))
    if before is None:
        abort(404)
    name = (request.form.get("name") or "").strip()
    if not name or len(name) > 80:
        flash("Category name is required (max 80 characters).", "error")
        return redirect(url_for("admin.list_categories"))
    if name == before["name"]:
        flash("No changes to save.", "info")
        return redirect(url_for("admin.list_categories"))
    try:
        execute("UPDATE Category SET name = %s WHERE categoryID = %s",
                (name, category_id))
    except IntegrityError:
        flash(f"A category named '{name}' already exists.", "error")
        return redirect(url_for("admin.list_categories"))
    log_action(session["user_id"], "Category", category_id, "Update",
               changes={"name": (before["name"], name)},
               ip=request.remote_addr)
    flash("Category renamed.", "success")
    return redirect(url_for("admin.list_categories"))


@bp.post("/categories/<int:category_id>/toggle")
@roles_required("Administrator")
def toggle_category(category_id):
    """Soft activate/deactivate. Hard delete is deliberately not offered:
    Ticket.categoryID is a foreign key, and historical tickets must keep
    their category (NFR-S6 / audit retention)."""
    before = query_one("SELECT * FROM Category WHERE categoryID = %s",
                       (category_id,))
    if before is None:
        abort(404)
    new_active = not bool(before["isActive"])
    if not new_active:
        active = query_one(
            "SELECT COUNT(*) AS n FROM Category WHERE isActive = TRUE", ())
        if active and active["n"] <= 1:
            flash("At least one category must stay active — ticket "
                  "submission and round-robin assignment depend on it.",
                  "error")
            return redirect(url_for("admin.list_categories"))
    execute("UPDATE Category SET isActive = %s WHERE categoryID = %s",
            (new_active, category_id))
    log_action(session["user_id"], "Category", category_id, "Update",
               changes={"isActive": (bool(before["isActive"]), new_active)},
               ip=request.remote_addr)
    flash(f"Category '{before['name']}' "
          f"{'activated' if new_active else 'deactivated'}. "
          + ("" if new_active else "Existing tickets keep it; it no longer "
             "appears on the submit form."),
          "success")
    return redirect(url_for("admin.list_categories"))


# ===========================================================================
# Panel 3 — SLA targets (FR-2.5 / FR-6.1)
# ===========================================================================

@bp.get("/sla")
@roles_required("Administrator")
def list_sla():
    rows = query_all("SELECT * FROM SLAPolicy", ())
    by_priority = {r["priority"]: r for r in rows}
    policies = [by_priority[p] for p in PRIORITIES if p in by_priority]
    return render_template("admin/sla.html", policies=policies)


@bp.post("/sla/<priority>")
@roles_required("Administrator")
def update_sla(priority):
    if priority not in PRIORITIES:
        abort(404)
    before = query_one("SELECT * FROM SLAPolicy WHERE priority = %s",
                       (priority,))
    if before is None:
        abort(404)

    def _mins(field):
        raw = (request.form.get(field) or "").strip()
        if not raw.isdigit():
            return None
        val = int(raw)
        return val if 1 <= val <= 129600 else None  # cap: 90 days

    response = _mins("responseTargetMins")
    resolution = _mins("resolutionTargetMins")
    if response is None or resolution is None:
        flash("Targets must be whole numbers of minutes between 1 and "
              "129600.", "error")
        return redirect(url_for("admin.list_sla"))
    if response > resolution:
        flash("The response target cannot exceed the resolution target.",
              "error")
        return redirect(url_for("admin.list_sla"))

    form = {"responseTargetMins": response,
            "resolutionTargetMins": resolution}
    changes = diff_fields(before, form,
                          ["responseTargetMins", "resolutionTargetMins"])
    if not changes:
        flash("No changes to save.", "info")
        return redirect(url_for("admin.list_sla"))

    execute("UPDATE SLAPolicy SET responseTargetMins = %s,"
            " resolutionTargetMins = %s WHERE priority = %s",
            (response, resolution, priority))
    log_action(session["user_id"], "SLAPolicy", 0, "Update",
               changes={f"{priority}.{k}": v for k, v in changes.items()},
               ip=request.remote_addr)
    flash(f"{priority} SLA targets updated. New tickets pick these up "
          "immediately.", "success")
    return redirect(url_for("admin.list_sla"))


# ===========================================================================
# Panel 4 — Audit log (FR-6.2, depends C3.2)
# ===========================================================================
#
# Read-only, Administrator-only, paginated. Every row can expand its
# field-level AuditLogChange children, so the trail reads as
# "Chris Karki updated Resource #12 — assetState: Available -> InUse"
# rather than just "Chris Karki updated". Audit rows are immutable
# (NFR-S6): this panel offers no edit or delete affordance, on purpose.

AUDIT_ENTITY_TYPES = ("Ticket", "Resource", "User", "KBArticle",
                      "TicketComment", "TicketResource",
                      "Category", "SLAPolicy")
AUDIT_ACTIONS = ("Create", "Update", "Delete", "Link", "Unlink")
AUDIT_PER_PAGE = 50

ACTION_BADGES = {
    "Create": "border: 1px solid var(--accent-color); color: var(--accent-color);",
    "Update": "border: 1px solid var(--warning-color); color: var(--warning-color);",
    "Delete": "border: 1px solid var(--danger-color); color: var(--danger-color);",
    "Link":   "border: 1px solid var(--panel-border); color: var(--text-primary);",
    "Unlink": "border: 1px solid var(--panel-border); color: var(--text-secondary);",
}


@bp.get("/audit")
@roles_required("Administrator")
def list_audit():
    # --- validate filters (whitelist, never interpolated: NFR-S4) -----
    f_entity = request.args.get("entity") or None
    f_entity = f_entity if f_entity in AUDIT_ENTITY_TYPES else None
    f_action = request.args.get("action") or None
    f_action = f_action if f_action in AUDIT_ACTIONS else None
    f_actor = request.args.get("actor") or ""
    f_actor = int(f_actor) if f_actor.isdigit() else None
    f_entity_id = (request.args.get("entity_id") or "").strip()
    f_entity_id = int(f_entity_id) if f_entity_id.isdigit() else None

    raw_page = request.args.get("page") or "1"
    page = int(raw_page) if raw_page.isdigit() else 1
    page = max(page, 1)

    where, params = " WHERE 1=1", []
    if f_entity:
        where += " AND a.entityType = %s"
        params.append(f_entity)
    if f_action:
        where += " AND a.action = %s"
        params.append(f_action)
    if f_actor is not None:
        where += " AND a.actorID = %s"
        params.append(f_actor)
    if f_entity_id is not None:
        where += " AND a.entityID = %s"
        params.append(f_entity_id)

    total_row = query_one(
        "SELECT COUNT(*) AS n FROM AuditLog a" + where, tuple(params))
    total = int(total_row["n"] or 0) if total_row else 0
    pages = max((total + AUDIT_PER_PAGE - 1) // AUDIT_PER_PAGE, 1)
    page = min(page, pages)
    offset = (page - 1) * AUDIT_PER_PAGE

    entries = query_all(
        "SELECT a.logID, a.actorID, a.entityType, a.entityID, a.action,"
        "       a.ipAddress, a.createdAt,"
        "       u.firstName, u.lastName, u.email"
        "  FROM AuditLog a"
        "  LEFT JOIN User u ON u.userID = a.actorID"
        + where +
        " ORDER BY a.logID DESC"
        " LIMIT %s OFFSET %s",
        tuple(params + [AUDIT_PER_PAGE, offset]))

    # --- field-level changes for the visible page, one query ----------
    changes_by_log = {}
    if entries:
        ids = [e["logID"] for e in entries]
        placeholders = ", ".join(["%s"] * len(ids))
        change_rows = query_all(
            "SELECT logID, fieldName, oldValue, newValue"
            "  FROM AuditLogChange"
            " WHERE logID IN (" + placeholders + ")"
            " ORDER BY changeID",
            tuple(ids))
        for row in change_rows:
            changes_by_log.setdefault(row["logID"], []).append(row)

    # --- actor dropdown: only users who actually have audit rows ------
    actors = query_all(
        "SELECT DISTINCT u.userID, u.firstName, u.lastName"
        "  FROM AuditLog a JOIN User u ON u.userID = a.actorID"
        " ORDER BY u.lastName, u.firstName", ())

    return render_template(
        "admin/audit.html",
        entries=entries, changes_by_log=changes_by_log,
        actors=actors,
        entity_types=AUDIT_ENTITY_TYPES, actions=AUDIT_ACTIONS,
        action_badges=ACTION_BADGES,
        f_entity=f_entity, f_action=f_action, f_actor=f_actor,
        f_entity_id=f_entity_id,
        page=page, pages=pages, total=total, per_page=AUDIT_PER_PAGE)

"""Resources blueprint — FR-3.1 resource inventory (cards H1.1 / H1.2).

Terminology note: "Resource", never "Asset" — locked project terminology.

Endpoints
    GET       /resources                     list + filter rail + sort + paginate + search
    GET       /resources/export              CSV export of the current filtered set
    POST      /resources/bulk                bulk update (status / assignment / location)
    GET/POST  /resources/new                 create
    GET       /resources/<id>                detail (tabbed: overview / tickets / activity)
    GET/POST  /resources/<id>/edit           full edit
    POST      /resources/<id>/properties     quick property update (detail right rail)
    POST      /resources/<id>/status         lifecycle change (kept for compatibility)

Access (FR-3.2): Technician, Manager, Administrator only. End Users get 403
— enforced by @roles_required on every route, not by hiding nav links.

FR-3.1 field set, all persisted and all editable:
    resourceTag (unique) · type · make · model · serialNumber · assignedUserID
    · status · location · purchaseDate · warrantyEndDate
    (+ createdAt / updatedAt maintained by the DB)

Every write goes through services.audit.log_action (FR-6.2). Updates record
field-level old/new values via diff_fields, which is what H1.2's acceptance
criterion checks. Bulk updates log one audit row per affected resource so the
per-entity history on the detail page stays complete.
"""

import csv
import io
from datetime import date, datetime

from flask import (Blueprint, Response, abort, flash, redirect,
                   render_template, request, session, url_for)
from mysql.connector.errors import IntegrityError

from ..db import execute, query_all, query_one
from ..services.audit import diff_fields, log_action, attach_changes
from .auth import roles_required

bp = Blueprint("resources", __name__, url_prefix="/resources")

STAFF = ("Technician", "Manager", "Administrator")

TYPES = ("Hardware", "Software", "Virtual")
STATUSES = ("InUse", "InStock", "Disposed", "LostMissing")

STATUS_LABELS = {"InUse": "In Use", "InStock": "In Stock",
                 "Disposed": "Disposed", "LostMissing": "Lost / Missing"}

# Badge styling per status, reusing the shared classes from main.css.
_MUTED = "border: 1px solid var(--panel-border); color: var(--text-secondary);"
_DANGER = "border: 1px solid var(--danger-color); color: var(--danger-color);"
STATUS_BADGES = {
    "InUse":       {"cls": "badge badge-status-open", "style": ""},
    "InStock":     {"cls": "badge badge-status-resolved", "style": ""},
    "Disposed":    {"cls": "badge", "style": _MUTED},
    "LostMissing": {"cls": "badge", "style": _DANGER},
}

# Only these fields are editable, and this is also the audit diff set.
EDITABLE = ["resourceTag", "type", "make", "model", "serialNumber",
            "assignedUserID", "status", "location", "purchaseDate",
            "warrantyEndDate"]

# Fields the detail-page "Edit Properties" quick panel may touch.
QUICK_FIELDS = ["status", "assignedUserID", "location"]

_FROM = (" FROM Resource r LEFT JOIN User u ON u.userID = r.assignedUserID")
_SELECT = ("SELECT r.*, CONCAT(u.firstName, ' ', u.lastName) AS assignedName"
           + _FROM)

PER_PAGE = 50

# Whitelisted sort keys -> ORDER BY column lists. Never interpolate raw
# request input into ORDER BY (NFR-S4); only these values are ever used.
SORTABLE = {
    "tag":      ["r.resourceTag"],
    "type":     ["r.type"],
    "model":    ["r.make", "r.model"],
    "serial":   ["r.serialNumber"],
    "assigned": ["assignedName"],
    "location": ["r.location"],
    "status":   ["r.status"],
    "warranty": ["r.warrantyEndDate"],
    "updated":  ["r.updatedAt"],
}


# ---------------------------------------------------------------------------
# Filtering (shared by list + export)
# ---------------------------------------------------------------------------

def _build_filters(args):
    """Translate query-string args into a WHERE fragment + bound params.

    Returns (where_sql, params, filters) where `filters` is the normalised
    dict handed to the template so the filter rail re-renders its state.
    """
    f_type = args.get("type") if args.get("type") in TYPES else None
    f_status = args.get("status") if args.get("status") in STATUSES else None
    f_warranty = args.get("warranty") if args.get("warranty") in ("active", "expired") else None
    f_assigned = (args.get("assigned") or "").strip() or None
    f_location = (args.get("location") or "").strip() or None
    q = (args.get("q") or "").strip() or None

    sql, params = " WHERE 1=1", []
    if f_type:
        sql += " AND r.type = %s"
        params.append(f_type)
    if f_status:
        sql += " AND r.status = %s"
        params.append(f_status)
    if f_warranty == "active":
        sql += " AND r.warrantyEndDate >= CURDATE()"
    elif f_warranty == "expired":
        sql += " AND r.warrantyEndDate < CURDATE()"
    if f_assigned:
        sql += " AND CONCAT(u.firstName, ' ', u.lastName) LIKE %s"
        params.append(f"%{f_assigned}%")
    if f_location:
        sql += " AND r.location LIKE %s"
        params.append(f"%{f_location}%")
    if q:
        sql += (" AND (r.resourceTag LIKE %s OR r.make LIKE %s OR r.model LIKE %s"
                "      OR r.serialNumber LIKE %s OR r.location LIKE %s)")
        params.extend([f"%{q}%"] * 5)

    filters = {"type": f_type, "status": f_status, "warranty": f_warranty,
               "assigned": f_assigned, "location": f_location, "q": q}
    return sql, params, filters


def _sort_clause(args):
    """Return (sort_key, direction, order_by_sql) from whitelisted values."""
    sort = args.get("sort") if args.get("sort") in SORTABLE else "tag"
    direction = "desc" if args.get("dir") == "desc" else "asc"
    cols = ", ".join(f"{c} {direction.upper()}" for c in SORTABLE[sort])
    # Stable tiebreaker so pagination never shuffles rows between pages.
    return sort, direction, f" ORDER BY {cols}, r.resourceID ASC"


# ---------------------------------------------------------------------------
# List / filter / search / sort / paginate
# ---------------------------------------------------------------------------

@bp.get("/")
@roles_required(*STAFF)
def list_resources():
    where, params, filters = _build_filters(request.args)
    sort, direction, order_by = _sort_clause(request.args)

    total = query_one("SELECT COUNT(*) AS n" + _FROM + where, tuple(params))["n"]

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    pages = max(1, -(-total // PER_PAGE))  # ceil division
    page = min(page, pages)
    offset = (page - 1) * PER_PAGE

    rows = query_all(_SELECT + where + order_by + " LIMIT %s OFFSET %s",
                     tuple(params) + (PER_PAGE, offset))

    return render_template(
        "resources/list.html",
        resources=[_shape(r) for r in rows],
        counts=_type_counts(),
        filters=filters, sort=sort, dir=direction,
        page=page, pages=pages, total=total,
        showing_from=(offset + 1) if total else 0,
        showing_to=min(offset + PER_PAGE, total),
        users=_assignable_users(),
        statuses=STATUSES, status_labels=STATUS_LABELS,
    )


def _type_counts():
    """Summary cards: total plus one count per type (Design Doc §5.5)."""
    rows = query_all("SELECT type, COUNT(*) AS n FROM Resource GROUP BY type")
    by_type = {r["type"]: r["n"] for r in rows}
    return {
        "total": sum(by_type.values()),
        "Hardware": by_type.get("Hardware", 0),
        "Software": by_type.get("Software", 0),
        "Virtual": by_type.get("Virtual", 0),
    }


# ---------------------------------------------------------------------------
# CSV export — exports the *filtered* set, not just the visible page
# ---------------------------------------------------------------------------

@bp.get("/export")
@roles_required(*STAFF)
def export_resources():
    where, params, _ = _build_filters(request.args)
    _, _, order_by = _sort_clause(request.args)
    rows = query_all(_SELECT + where + order_by, tuple(params))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Resource Tag", "Type", "Make", "Model", "Serial Number",
                     "Assigned User", "Status", "Location", "Purchase Date",
                     "Warranty End Date", "Created At", "Updated At"])
    for r in rows:
        writer.writerow([
            r["resourceTag"], r["type"], r["make"], r["model"],
            r["serialNumber"] or "", r["assignedName"] or "Unassigned",
            STATUS_LABELS[r["status"]], r["location"],
            r["purchaseDate"] or "", r["warrantyEndDate"] or "",
            r["createdAt"] or "", r["updatedAt"] or "",
        ])

    filename = f"resources-{datetime.now():%Y%m%d-%H%M}.csv"
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"})


# ---------------------------------------------------------------------------
# Bulk update — Freshservice-style "Bulk Update" over the checkbox selection
# ---------------------------------------------------------------------------

@bp.post("/bulk")
@roles_required(*STAFF)
def bulk_update():
    """Apply one change (status, assignment, or location) to many resources.

    Deliberately NOT a hard delete: TicketResource and AuditLog reference
    Resource, so removal would orphan ticket/audit history (FR-6.2). The
    lifecycle equivalent of delete is a bulk status change to Disposed.
    Each affected row gets its own audit entry with old/new values.
    """
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()][:200]
    action = request.form.get("action")
    raw = (request.form.get("value") or "").strip()

    # Only ever redirect back to our own list view.
    ret = request.form.get("return") or ""
    dest = ret if ret.startswith("/") and not ret.startswith("//") \
        else url_for("resources.list_resources")

    if not ids:
        flash("Select at least one resource first.", "error")
        return redirect(dest)

    if action == "status":
        if raw not in STATUSES:
            flash("Unknown resource status.", "error")
            return redirect(dest)
        field, value = "status", raw
    elif action == "assign":
        if raw and raw.isdigit():
            user = query_one(
                "SELECT userID FROM User WHERE userID = %s AND status = 'Active'",
                (int(raw),))
            if user is None:
                flash("Selected user is not an active account.", "error")
                return redirect(dest)
            field, value = "assignedUserID", int(raw)
        else:
            field, value = "assignedUserID", None  # bulk unassign
    elif action == "location":
        if not raw:
            flash("Enter a location to apply.", "error")
            return redirect(dest)
        if len(raw) > 120:
            flash("Location must be 120 characters or fewer.", "error")
            return redirect(dest)
        field, value = "location", raw
    else:
        flash("Unknown bulk action.", "error")
        return redirect(dest)

    updated = 0
    for rid in ids:
        before = query_one("SELECT * FROM Resource WHERE resourceID = %s", (rid,))
        if before is None or before[field] == value:
            continue
        execute(f"UPDATE Resource SET {field} = %s WHERE resourceID = %s",
                (value, rid))
        log_action(session["user_id"], "Resource", rid, "Update",
                   changes={field: (before[field], value)})
        updated += 1

    flash(f"{updated} resource{'s' if updated != 1 else ''} updated." if updated
          else "No changes were needed.", "success" if updated else "info")
    return redirect(dest)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@bp.route("/new", methods=["GET", "POST"])
@roles_required(*STAFF)
def new_resource():
    if request.method == "POST":
        form = _read_form(request.form)
        errors = _validate(form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("resources/form.html", resource=form,
                                   users=_assignable_users(), mode="new",
                                   types=TYPES, statuses=STATUSES,
                                   status_labels=STATUS_LABELS), 400
        try:
            new_id = execute(
                "INSERT INTO Resource (resourceTag, type, make, model, serialNumber,"
                "  assignedUserID, status, location, purchaseDate, warrantyEndDate)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(form[k] for k in EDITABLE))
        except IntegrityError as exc:
            # resourceTag is UNIQUE in the schema; surface it as a field error
            # rather than a 500. The DB is the source of truth here, not a
            # pre-check — a pre-check would still race under concurrency.
            if "resourceTag" in str(exc) or "Duplicate" in str(exc):
                flash(f"Resource tag '{form['resourceTag']}' already exists. "
                      "Tags must be unique.", "error")
            else:
                flash("Could not save the resource. Check the values and retry.", "error")
            return render_template("resources/form.html", resource=form,
                                   users=_assignable_users(), mode="new",
                                   types=TYPES, statuses=STATUSES,
                                   status_labels=STATUS_LABELS), 400

        log_action(session["user_id"], "Resource", new_id, "Create")
        flash(f"Resource {form['resourceTag']} created.", "success")
        return redirect(url_for("resources.view_resource", resource_id=new_id))

    blank = {k: None for k in EDITABLE}
    blank.update(type="Hardware", status="InStock")
    return render_template("resources/form.html", resource=blank,
                           users=_assignable_users(), mode="new",
                           types=TYPES, statuses=STATUSES,
                           status_labels=STATUS_LABELS)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@bp.get("/<int:resource_id>")
@roles_required(*STAFF)
def view_resource(resource_id):
    row = query_one(_SELECT + " WHERE r.resourceID = %s", (resource_id,))
    if row is None:
        abort(404)

    # FR-3.2: the resource detail view exposes the full history of linked
    # tickets. TicketResource rows exist from seed_demo; the linking UI
    # itself is H2.1.
    tickets = query_all(
        "SELECT t.ticketID, t.title, t.status, t.priority, tr.linkedAt"
        "  FROM TicketResource tr JOIN Ticket t ON t.ticketID = tr.ticketID"
        " WHERE tr.resourceID = %s ORDER BY tr.linkedAt DESC", (resource_id,))

    history = query_all(
        "SELECT a.logID, a.action, a.entityType, a.timestamp,"
        "       CONCAT(u.firstName,' ',u.lastName) AS actor"
        "  FROM AuditLog a JOIN User u ON u.userID = a.actorID"
        " WHERE a.entityType = 'Resource' AND a.entityID = %s"
        " ORDER BY a.timestamp DESC LIMIT 15", (resource_id,))
    attach_changes(history, noun="resource")
    
    return render_template("resources/detail.html", r=_shape(row),
                           tickets=tickets, history=history,
                           users=_assignable_users(),
                           statuses=STATUSES, status_labels=STATUS_LABELS)


# ---------------------------------------------------------------------------
# Quick property update — backs the "Edit Properties" panel on the detail page
# ---------------------------------------------------------------------------

@bp.post("/<int:resource_id>/properties")
@roles_required(*STAFF)
def update_properties(resource_id):
    before = query_one("SELECT * FROM Resource WHERE resourceID = %s", (resource_id,))
    if before is None:
        abort(404)

    status = request.form.get("status")
    location = (request.form.get("location") or "").strip()
    assigned_raw = (request.form.get("assignedUserID") or "").strip()

    errors = []
    if status not in STATUSES:
        errors.append("Status must be one of: In Use, In Stock, Disposed, Lost/Missing.")
    if not location:
        errors.append("Location is required.")
    elif len(location) > 120:
        errors.append("Location must be 120 characters or fewer.")

    assigned = None
    if assigned_raw:
        if not assigned_raw.isdigit():
            errors.append("Invalid assigned user.")
        else:
            user = query_one(
                "SELECT userID FROM User WHERE userID = %s AND status = 'Active'",
                (int(assigned_raw),))
            if user is None:
                errors.append("Assigned user must be an active account.")
            else:
                assigned = int(assigned_raw)

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("resources.view_resource", resource_id=resource_id))

    after = {"status": status, "assignedUserID": assigned, "location": location}
    changes = diff_fields(before, after, QUICK_FIELDS)
    if changes:
        execute(
            "UPDATE Resource SET status=%s, assignedUserID=%s, location=%s"
            " WHERE resourceID=%s",
            (status, assigned, location, resource_id))
        log_action(session["user_id"], "Resource", resource_id, "Update",
                   changes=changes)
    flash("Properties updated." if changes else "No changes to save.",
          "success" if changes else "info")
    return redirect(url_for("resources.view_resource", resource_id=resource_id))


# ---------------------------------------------------------------------------
# Edit  (H1.2)
# ---------------------------------------------------------------------------

@bp.route("/<int:resource_id>/edit", methods=["GET", "POST"])
@roles_required(*STAFF)
def edit_resource(resource_id):
    before = query_one("SELECT * FROM Resource WHERE resourceID = %s", (resource_id,))
    if before is None:
        abort(404)

    def _name_for(uid):
        """Picker button label for a user ID (None-safe)."""
        if not uid:
            return None
        row = query_one("SELECT CONCAT(firstName, ' ', lastName) AS name"
                        "  FROM User WHERE userID = %s", (uid,))
        return row["name"] if row else None

    if request.method == "POST":
        form = _read_form(request.form)
        errors = _validate(form)
        if errors:
            for e in errors:
                flash(e, "error")
            form["resourceID"] = resource_id
            return render_template("resources/form.html", resource=form,
                                   assigned_name=_name_for(form["assignedUserID"]),
                                   users=_assignable_users(), mode="edit",
                                   types=TYPES, statuses=STATUSES,
                                   status_labels=STATUS_LABELS), 400
        try:
            execute(
                "UPDATE Resource SET resourceTag=%s, type=%s, make=%s, model=%s,"
                "  serialNumber=%s, assignedUserID=%s, status=%s, location=%s,"
                "  purchaseDate=%s, warrantyEndDate=%s"
                " WHERE resourceID=%s",
                (*[form[k] for k in EDITABLE], resource_id))
        except IntegrityError as exc:
            if "resourceTag" in str(exc) or "Duplicate" in str(exc):
                flash(f"Resource tag '{form['resourceTag']}' is already in use.", "error")
            else:
                flash("Could not save the resource. Check the values and retry.", "error")
            form["resourceID"] = resource_id
            return render_template("resources/form.html", resource=form,
                                   assigned_name=_name_for(form["assignedUserID"]),
                                   users=_assignable_users(), mode="edit",
                                   types=TYPES, statuses=STATUSES,
                                   status_labels=STATUS_LABELS), 400

        changes = diff_fields(before, form, EDITABLE)
        if changes:
            log_action(session["user_id"], "Resource", resource_id, "Update",
                       changes=changes, ip=request.remote_addr)
        flash("Resource updated." if changes else "No changes to save.",
              "success" if changes else "info")
        return redirect(url_for("resources.view_resource", resource_id=resource_id))

    return render_template("resources/form.html", resource=before,
                           assigned_name=_name_for(before["assignedUserID"]),
                           users=_assignable_users(), mode="edit",
                           types=TYPES, statuses=STATUSES,
                           status_labels=STATUS_LABELS)


@bp.post("/<int:resource_id>/status")
@roles_required(*STAFF)
def change_status(resource_id):
    """Lifecycle status change — kept for backwards compatibility with any
    template or test that posts here. New UI paths use update_properties
    (detail rail) and bulk_update (list)."""
    before = query_one("SELECT * FROM Resource WHERE resourceID = %s", (resource_id,))
    if before is None:
        abort(404)

    new_status = request.form.get("status")
    if new_status not in STATUSES:
        flash("Unknown resource status.", "error")
        return redirect(url_for("resources.view_resource", resource_id=resource_id))

    if new_status != before["status"]:
        execute("UPDATE Resource SET status = %s WHERE resourceID = %s",
                (new_status, resource_id))
        log_action(session["user_id"], "Resource", resource_id, "Update",
                   changes={"status": (before["status"], new_status)})
        flash(f"{before['resourceTag']} is now {STATUS_LABELS[new_status]}.", "success")
    return redirect(url_for("resources.view_resource", resource_id=resource_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_form(form):
    """Normalise form input: empty strings become None so NULLable columns
    store NULL rather than ''."""
    def val(key):
        v = (form.get(key) or "").strip()
        return v or None

    assigned = val("assignedUserID")
    return {
        "resourceTag": val("resourceTag"),
        "type": val("type"),
        "make": val("make"),
        "model": val("model"),
        "serialNumber": val("serialNumber"),
        "assignedUserID": int(assigned) if assigned and assigned.isdigit() else None,
        "status": val("status"),
        "location": val("location"),
        "purchaseDate": val("purchaseDate"),
        "warrantyEndDate": val("warrantyEndDate"),
    }


def _validate(f):
    """Server-side validation (NFR-S4). The DB enforces these too; this layer
    exists to return friendly messages instead of 500s."""
    errors = []
    if not f["resourceTag"]:
        errors.append("Resource tag is required.")
    elif len(f["resourceTag"]) > 40:
        errors.append("Resource tag must be 40 characters or fewer.")
    if f["type"] not in TYPES:
        errors.append("Type must be Hardware, Software, or Virtual.")
    if not f["make"]:
        errors.append("Make is required.")
    if not f["model"]:
        errors.append("Model is required.")
    if f["status"] not in STATUSES:
        errors.append("Status must be one of: In Use, In Stock, Disposed, Lost/Missing.")
    if not f["location"]:
        errors.append("Location is required.")
    if f["purchaseDate"] and f["warrantyEndDate"] and \
            f["warrantyEndDate"] < f["purchaseDate"]:
        # Mirrors the CHECK constraint in schema.sql (§4.3 range control).
        errors.append("Warranty end date cannot be earlier than the purchase date.")
    return errors


def _assignable_users():
    return query_all(
        "SELECT userID, CONCAT(firstName,' ',lastName) AS name, role FROM User"
        " WHERE status = 'Active' ORDER BY firstName")


def _shape(r):
    """Attach presentation data so templates stay logic-free."""
    warranty = r["warrantyEndDate"]
    r = dict(r)
    r["status_label"] = STATUS_LABELS[r["status"]]
    r["status_badge"] = STATUS_BADGES[r["status"]]
    r["under_warranty"] = bool(warranty and warranty >= date.today())
    r["warranty_label"] = (
        warranty.strftime("%b %d, %Y") if warranty else "Not tracked")
    return r


@bp.get("/users/search")
@roles_required(*STAFF)
def search_users():
    """JSON search behind the Used By / Assigned User picker.

    Returns at most 20 active users matching q across name, email, and
    role. Server-side search means the full user table never reaches the
    browser (same pattern as tickets.search_linkable). Empty q returns
    the first 20 by first name so the picker isn't blank on open.
    """
    q = (request.args.get("q") or "").strip()

    sql = ("SELECT userID, CONCAT(firstName, ' ', lastName) AS name,"
           "       UPPER(CONCAT(LEFT(firstName,1), LEFT(lastName,1))) AS initials,"
           "       role, email"
           "  FROM User WHERE status = 'Active'")
    params = []
    if q:
        sql += ("   AND (CONCAT(firstName, ' ', lastName) LIKE %s"
                "        OR email LIKE %s OR role LIKE %s)")
        params.extend([f"%{q}%"] * 3)

    rows = query_all(sql + " ORDER BY firstName, lastName LIMIT 20",
                     tuple(params))
    return {"results": [
        {"userID": u["userID"], "name": u["name"], "initials": u["initials"],
         "role": u["role"], "email": u["email"]}
        for u in rows
    ]}

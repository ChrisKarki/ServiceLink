"""Resources blueprint — FR-3.1 resource inventory (cards H1.1 / H1.2).

Terminology note: "Resource", never "Asset" — locked project terminology.

Endpoints
    GET       /resources                 list + filter (type/status/warranty) + search
    GET/POST  /resources/new             create
    GET       /resources/<id>            detail (incl. linked ticket history)
    GET/POST  /resources/<id>/edit       edit
    POST      /resources/<id>/status     lifecycle change (used by Decommission)

Access (FR-3.2): Technician, Manager, Administrator only. End Users get 403
— enforced by @roles_required on every route, not by hiding nav links.

FR-3.1 field set, all persisted and all editable:
    resourceTag (unique) · type · make · model · serialNumber · assignedUserID
    · status · location · purchaseDate · warrantyEndDate
    (+ createdAt / updatedAt maintained by the DB)

Every write goes through services.audit.log_action (FR-6.2). Updates record
field-level old/new values via diff_fields, which is what H1.2's acceptance
criterion checks.
"""

from datetime import date

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, session, url_for)
from mysql.connector.errors import IntegrityError

from ..db import execute, query_all, query_one
from ..services.audit import diff_fields, log_action
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
_WARN = "border: 1px solid var(--warning-color); color: var(--warning-color);"
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

_SELECT = (
    "SELECT r.*, CONCAT(u.firstName, ' ', u.lastName) AS assignedName"
    "  FROM Resource r LEFT JOIN User u ON u.userID = r.assignedUserID")


# ---------------------------------------------------------------------------
# List / filter / search
# ---------------------------------------------------------------------------

@bp.get("/")
@roles_required(*STAFF)
def list_resources():
    """Filter by type, status, and warranty state; search across tag, make,
    model, serial, and location. All values bound as parameters (NFR-S4)."""
    f_type = request.args.get("type") if request.args.get("type") in TYPES else None
    f_status = request.args.get("status") if request.args.get("status") in STATUSES else None
    f_warranty = request.args.get("warranty")
    q = (request.args.get("q") or "").strip()

    sql, params = _SELECT + " WHERE 1=1", []
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
    if q:
        sql += (" AND (r.resourceTag LIKE %s OR r.make LIKE %s OR r.model LIKE %s"
                "      OR r.serialNumber LIKE %s OR r.location LIKE %s)")
        params.extend([f"%{q}%"] * 5)

    rows = query_all(sql + " ORDER BY r.resourceTag", tuple(params))

    return render_template(
        "resources/list.html",
        resources=[_shape(r) for r in rows],
        counts=_type_counts(),
        filters={"type": f_type, "status": f_status, "warranty": f_warranty, "q": q},
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
        "SELECT a.action, a.timestamp, CONCAT(u.firstName,' ',u.lastName) AS actor"
        "  FROM AuditLog a JOIN User u ON u.userID = a.actorID"
        " WHERE a.entityType = 'Resource' AND a.entityID = %s"
        " ORDER BY a.timestamp DESC LIMIT 5", (resource_id,))

    return render_template("resources/detail.html", r=_shape(row),
                           tickets=tickets, history=history,
                           statuses=STATUSES, status_labels=STATUS_LABELS)


# ---------------------------------------------------------------------------
# Edit  (H1.2)
# ---------------------------------------------------------------------------

@bp.route("/<int:resource_id>/edit", methods=["GET", "POST"])
@roles_required(*STAFF)
def edit_resource(resource_id):
    before = query_one("SELECT * FROM Resource WHERE resourceID = %s", (resource_id,))
    if before is None:
        abort(404)

    if request.method == "POST":
        form = _read_form(request.form)
        errors = _validate(form)
        if errors:
            for e in errors:
                flash(e, "error")
            form["resourceID"] = resource_id
            return render_template("resources/form.html", resource=form,
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
                                   users=_assignable_users(), mode="edit",
                                   types=TYPES, statuses=STATUSES,
                                   status_labels=STATUS_LABELS), 400

        changes = diff_fields(before, form, EDITABLE)
        if changes:
            log_action(session["user_id"], "Resource", resource_id, "Update",
                       changes=changes)
        flash("Resource updated." if changes else "No changes to save.",
              "success" if changes else "info")
        return redirect(url_for("resources.view_resource", resource_id=resource_id))

    return render_template("resources/form.html", resource=before,
                           users=_assignable_users(), mode="edit",
                           types=TYPES, statuses=STATUSES,
                           status_labels=STATUS_LABELS)


@bp.post("/<int:resource_id>/status")
@roles_required(*STAFF)
def change_status(resource_id):
    """Lifecycle status change — backs the Decommission action on the detail
    page. A real, audited status transition, not a cosmetic toast."""
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

"""Audit service — the single write path for the audit trail (FR-6.2, NFR-S6).

Every create, update, delete, link, and unlink in ServiceLink MUST go
through log_action(). No module writes its own AuditLog INSERT — one
helper, one format, one transaction.

Usage (Prabh, Hiten — import this, do not reimplement):

    from ..services.audit import log_action, diff_fields

    # Create — no field changes to record:
    log_action(session["user_id"], "Ticket", ticket_id, "Create")

    # Update — record field-level old/new values (H1.2 acceptance
    # criterion depends on these rows existing):
    changes = diff_fields(before_row, form_data,
                          fields=["status", "assignedUserID", "location"])
    if changes:
        log_action(session["user_id"], "Resource", resource_id,
                   "Update", changes=changes)

    # Resource linking (UC-03):
    log_action(session["user_id"], "TicketResource", ticket_id, "Link")

Both AuditLog and its AuditLogChange child rows are written in ONE
transaction: either the full audit record persists or none of it does.
Audit rows are immutable once written and are never deleted (NFR-S6) —
there is deliberately no update or delete function in this module.
"""

from flask import has_request_context, request

from ..db import get_connection, query_all

# Mirror the ENUM domains in schema.sql — fail loudly on drift instead of
# letting MariaDB truncate an out-of-domain value into garbage.
ENTITY_TYPES = {"Ticket", "Resource", "User", "KBArticle",
                "TicketComment", "TicketResource",
                "Category", "SLAPolicy"}
ACTIONS = {"Create", "Update", "Delete", "Link", "Unlink"}


def log_action(actor_id, entity_type, entity_id, action, changes=None, ip=None):
    """Write one audit event (plus optional field-level changes) atomically.

    Args:
        actor_id:    User.userID performing the action (session["user_id"]).
        entity_type: One of ENTITY_TYPES.
        entity_id:   PK of the affected record. For TicketResource
                     (composite PK) pass the ticketID.
        action:      One of ACTIONS.
        changes:     Optional dict {fieldName: (old_value, new_value)}.
                     Values are stringified; None is stored as SQL NULL.
        ip:          Optional override. Defaults to request.remote_addr
                     inside a request, "127.0.0.1" outside one (CLI, tests).

    Returns:
        The new AuditLog.logID.
    """
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"Unknown audit entityType: {entity_type!r}")
    if action not in ACTIONS:
        raise ValueError(f"Unknown audit action: {action!r}")

    if ip is None:
        ip = request.remote_addr if has_request_context() else None
        ip = ip or "127.0.0.1"  # remote_addr is None under the test client

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO AuditLog (actorID, entityType, entityID, action, ipAddress)"
            " VALUES (%s, %s, %s, %s, %s)",
            (actor_id, entity_type, entity_id, action, ip),
        )
        log_id = cur.lastrowid

        if changes:
            rows = [
                (
                    log_id,
                    field,
                    None if old is None else str(old),
                    None if new is None else str(new),
                )
                for field, (old, new) in changes.items()
            ]
            cur.executemany(
                "INSERT INTO AuditLogChange (logID, fieldName, oldValue, newValue)"
                " VALUES (%s, %s, %s, %s)",
                rows,
            )

        conn.commit()
        cur.close()
        return log_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def diff_fields(before, after, fields):
    """Build a log_action() changes dict from a before-row and new values.

    Args:
        before: dict of the row as it was (e.g. from query_one).
        after:  dict of incoming values (e.g. validated form data).
        fields: which keys to compare.

    Returns:
        {field: (old, new)} for fields whose value actually changed.
        Empty dict if nothing changed — skip log_action("Update") then.
    """
    changes = {}
    for field in fields:
        old, new = before.get(field), after.get(field)
        # Compare as strings so 3 == "3" from form data doesn't log noise.
        old_cmp = None if old is None else str(old)
        new_cmp = None if new is None else str(new)
        if old_cmp != new_cmp:
            changes[field] = (old, new)
    return changes

_VERBS = {"Create": "created", "Update": "updated", "Delete": "deleted",
          "Link": "linked", "Unlink": "unlinked"}


def attach_changes(history):
    """Enrich detail-page activity rows in place for FR-6.2 rendering.

    Each row must carry logID and action. Adds:
        verb     past-tense action ("updated", not "updated"+"d" hacks)
        changes  [{fieldName, oldValue, newValue}] from AuditLogChange
    One batched IN query — no N+1 against the audit table.
    """
    for h in history:
        h["verb"] = _VERBS.get(h["action"], h["action"].lower())
        h["changes"] = []
    ids = [h["logID"] for h in history]
    if not ids:
        return history
    placeholders = ", ".join(["%s"] * len(ids))
    rows = query_all(
        "SELECT logID, fieldName, oldValue, newValue"
        "  FROM AuditLogChange"
        " WHERE logID IN (" + placeholders + ")"
        " ORDER BY logID, fieldName",  # PK is (logID, fieldName)
        tuple(ids))
    by_log = {}
    for r in rows:
        by_log.setdefault(r["logID"], []).append(r)
    for h in history:
        h["changes"] = by_log.get(h["logID"], [])
    return history

import re

# ---------------------------------------------------------------------------
# Detail-page activity humanizer (FR-6.2)
# ---------------------------------------------------------------------------
# Turns raw AuditLog/AuditLogChange rows into sentences a person can read:
#   "Chris Karki assigned this ticket to Hiten Lamba"
#   "Katie Nguyen changed status from Assigned to In Progress"
# ID-bearing fields are resolved to display names via batched lookups —
# never one query per row.

FIELD_LABELS = {
    "assignedToUserID": "Assigned to", "assignedUserID": "Assigned to",
    "submittedByUserID": "Submitted by", "authorUserID": "Author",
    "categoryID": "Category", "linkedKBArticleID": "Resolution article",
    "resourceID": "Resource", "status": "Status", "priority": "Priority",
    "location": "Location", "title": "Title", "name": "Name", "role": "Role",
    "isActive": "Active", "resourceTag": "Resource tag", "type": "Type",
    "make": "Make", "model": "Model", "serialNumber": "Serial number",
    "purchaseDate": "Purchase date", "warrantyEndDate": "Warranty end",
    "resolutionNotes": "Resolution notes", "commentType": "Comment type",
    "responseTargetMins": "Response target (mins)",
    "resolutionTargetMins": "Resolution target (mins)",
}

_USER_FIELDS = {"assignedToUserID", "assignedUserID",
                "submittedByUserID", "authorUserID"}

_VERBS = {"Create": "created", "Update": "updated", "Delete": "deleted",
          "Link": "linked", "Unlink": "unlinked"}

_NOUNS = {"Ticket": "ticket", "Resource": "resource", "User": "account",
          "Category": "category", "SLAPolicy": "SLA policy",
          "KBArticle": "article"}


def _pretty_field(name):
    base = name.split(".")[-1]  # SLA rows are "High.responseTargetMins"
    label = FIELD_LABELS.get(base)
    if label is None:
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
        label = spaced[:1].upper() + spaced[1:].lower()
    if "." in name:  # keep the priority prefix for SLA rows
        label = name.split(".")[0] + " " + label[:1].lower() + label[1:]
    return label


def _pretty_value(v):
    """CamelCase enum values -> spaced ('InProgress' -> 'In Progress')."""
    if v in ("True", "False"):
        return "Yes" if v == "True" else "No"
    if re.fullmatch(r"[A-Za-z]{2,}", v) and not v.islower() and not v.isupper():
        return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", v)
    return v


def _load_labels(ids, sql):
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))
    return {r["id"]: r["label"]
            for r in query_all(sql.format(ph=ph), tuple(ids))}


def attach_changes(history, noun=None):
    """Enrich detail-page activity rows in place.

    Rows must carry logID, action, actor, and (recommended) entityType.
    Adds per row:
        verb     past-tense action
        summary  full human sentence, IDs resolved to names
        changes  [{label, old, new}] display-ready field changes
    """
    by_log = {}
    for h in history:
        h["verb"] = _VERBS.get(h["action"], h["action"].lower())
        h["changes"] = []
    ids = [h["logID"] for h in history]
    if ids:
        ph = ", ".join(["%s"] * len(ids))
        rows = query_all(
            "SELECT logID, fieldName, oldValue, newValue"
            "  FROM AuditLogChange"
            " WHERE logID IN (" + ph + ")"
            " ORDER BY logID, fieldName",  # PK is (logID, fieldName)
            tuple(ids))
        for r in rows:
            by_log.setdefault(r["logID"], []).append(r)

    # -- collect referenced IDs, one pass ------------------------------
    want = {"user": set(), "cat": set(), "kb": set(), "res": set()}
    for rows in by_log.values():
        for r in rows:
            f = r["fieldName"].split(".")[-1]
            for v in (r["oldValue"], r["newValue"]):
                if not (v and v.isdigit()):
                    continue
                if f in _USER_FIELDS:
                    want["user"].add(int(v))
                elif f == "categoryID":
                    want["cat"].add(int(v))
                elif f == "linkedKBArticleID":
                    want["kb"].add(int(v))
                elif f == "resourceID":
                    want["res"].add(int(v))

    labels = {
        "user": _load_labels(want["user"],
            "SELECT userID AS id, CONCAT(firstName, ' ', lastName) AS label"
            "  FROM User WHERE userID IN ({ph})"),
        "cat": _load_labels(want["cat"],
            "SELECT categoryID AS id, name AS label"
            "  FROM Category WHERE categoryID IN ({ph})"),
        "kb": _load_labels(want["kb"],
            "SELECT articleID AS id, title AS label"
            "  FROM KBArticle WHERE articleID IN ({ph})"),
        "res": _load_labels(want["res"],
            "SELECT resourceID AS id, resourceTag AS label"
            "  FROM Resource WHERE resourceID IN ({ph})"),
    }

    def _display(field, v):
        f = field.split(".")[-1]
        if v is None or v == "" or v == "None":
            return "Unassigned" if f in _USER_FIELDS else "—"
        if v.isdigit():
            i = int(v)
            if f in _USER_FIELDS:
                return labels["user"].get(i, f"user #{i}")
            if f == "categoryID":
                return labels["cat"].get(i, f"category #{i}")
            if f == "linkedKBArticleID":
                return labels["kb"].get(i, f"article #{i}")
            if f == "resourceID":
                return labels["res"].get(i, f"resource #{i}")
        return _pretty_value(v)

    for h in history:
        disp = [{"label": _pretty_field(r["fieldName"]),
                 "old": _display(r["fieldName"], r["oldValue"]),
                 "new": _display(r["fieldName"], r["newValue"])}
                for r in by_log.get(h["logID"], [])]
        h["changes"] = disp
        h["summary"] = _summary(h, disp, noun)
    return history


def _summary(h, disp, noun):
    actor = h.get("actor") or "Someone"
    etype = h.get("entityType")
    action = h["action"]
    noun = noun or _NOUNS.get(etype, "record")

    if etype == "TicketComment":
        return f"{actor} added a comment"
    if etype == "TicketResource":
        target = disp[0]["new"] if disp and action == "Link" else \
                 (disp[0]["old"] if disp else None)
        thing = target if target and target != "—" else "a resource"
        return (f"{actor} linked {thing}" if action == "Link"
                else f"{actor} unlinked {thing}")
    if action == "Create":
        return (f"{actor} submitted this ticket" if noun == "ticket"
                else f"{actor} created this {noun}")
    if len(disp) == 1:
        c = disp[0]
        low = c["label"].lower()
        if low == "assigned to":
            if c["new"] == "Unassigned":
                return f"{actor} unassigned this {noun}"
            return f"{actor} assigned this {noun} to {c['new']}"
        return f"{actor} changed {low} from {c['old']} to {c['new']}"
    if disp:
        return f"{actor} updated {len(disp)} fields"
    return f"{actor} {h['verb']} this {noun}"
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

from ..db import get_connection

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

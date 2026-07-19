"""Team management (C3.2) — Manager/Administrator roster + workload.

Read-only in this sprint: one row per Technician/Manager showing account
status, last login, current open-ticket load (drives whether round-robin
is balanced), and resolved totals. Mutations (role/status changes) stay
in the Administration console — one write path, one audit trail.

Same schema-drift defence as reports.py: SELECT * over Ticket and
resolve the assignee key in Python from candidates, aggregate in Python.
"""

from flask import Blueprint, render_template

from ..db import query_all
from .auth import roles_required

bp = Blueprint("team", __name__, url_prefix="/team")

OPEN_STATUSES = {"New", "Assigned", "In Progress", "Waiting on User"}
ASSIGNEE_KEYS = ("assignedUserID", "assignedTo", "technicianID",
                 "assigneeID", "assignedTechID", "assigned_user_id")


def _assignee(ticket):
    for k in ASSIGNEE_KEYS:
        if k in ticket and ticket[k] is not None:
            return ticket[k]
    return None


@bp.get("/")
@roles_required("Manager", "Administrator")
def index():
    members = query_all(
        "SELECT userID, firstName, lastName, email, role, status, lastLoginAt"
        "  FROM User"
        " WHERE role IN ('Technician', 'Manager')"
        " ORDER BY role DESC, lastName, firstName", ())

    tickets = query_all("SELECT * FROM Ticket", ())

    load = {}  # userID -> {"open": n, "in_progress": n, "resolved": n}
    for t in tickets:
        uid = _assignee(t)
        if uid is None:
            continue
        row = load.setdefault(uid, {"open": 0, "in_progress": 0,
                                    "resolved": 0})
        status = t.get("status")
        if status in OPEN_STATUSES:
            row["open"] += 1
            if status == "In Progress":
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

    return render_template("team/index.html", members=members,
                           unassigned_open=unassigned_open)

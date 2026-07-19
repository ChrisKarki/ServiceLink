"""Reports (C3.1) — Manager/Administrator operational metrics.

Read-only aggregations over Ticket:
    - open vs resolved/closed headline counts
    - breakdown by status, priority, and category
    - average resolution time (resolvedAt - created, resolved tickets only)
    - SLA context: counts per priority next to their targets

Implementation note (schema-drift defence): three separate 500 cycles in
this project came from assuming column names that differed on the
deployed schema. This module therefore does SELECT * and resolves the
assignee / created-timestamp keys in Python from candidate lists, then
aggregates in Python. At course-project scale (hundreds of tickets)
this is fine; if it ever isn't, replace with GROUP BY once schema.sql
is the verified source of truth.
"""

from datetime import datetime

from flask import Blueprint, render_template

from ..db import query_all
from .auth import roles_required

bp = Blueprint("reports", __name__, url_prefix="/reports")

STATUSES = ("New", "Assigned", "In Progress", "Waiting on User",
            "Resolved", "Closed")
PRIORITIES = ("Critical", "High", "Medium", "Low")
OPEN_STATUSES = {"New", "Assigned", "In Progress", "Waiting on User"}

# Candidate key names, most likely first (memory of prior drift cycles).
CREATED_KEYS = ("createdAt", "submittedAt", "created_at", "dateCreated",
                "openedAt", "createdOn")
RESOLVED_KEYS = ("resolvedAt", "resolved_at", "dateResolved", "closedAt")


def _get(row, candidates):
    for k in candidates:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _bars(counter, order):
    """[(label, count, pct_width)] for simple CSS bar rendering."""
    total = sum(counter.get(k, 0) for k in order) or 1
    return [(k, counter.get(k, 0),
             round(counter.get(k, 0) * 100 / total)) for k in order]


@bp.get("/")
@roles_required("Manager", "Administrator")
def index():
    tickets = query_all("SELECT * FROM Ticket", ())
    cat_rows = query_all("SELECT categoryID, name FROM Category", ())
    cat_names = {c["categoryID"]: c["name"] for c in cat_rows}
    sla_rows = query_all("SELECT * FROM SLAPolicy", ())
    sla = {r["priority"]: r for r in sla_rows}

    by_status, by_priority, by_category = {}, {}, {}
    open_count = resolved_count = 0
    resolution_deltas = []

    for t in tickets:
        status = t.get("status")
        priority = t.get("priority")
        by_status[status] = by_status.get(status, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1
        cat = cat_names.get(t.get("categoryID"), "Uncategorised")
        by_category[cat] = by_category.get(cat, 0) + 1

        if status in OPEN_STATUSES:
            open_count += 1
        elif status in ("Resolved", "Closed"):
            resolved_count += 1

        created = _get(t, CREATED_KEYS)
        resolved = _get(t, RESOLVED_KEYS)
        if (isinstance(created, datetime) and isinstance(resolved, datetime)
                and resolved >= created):
            resolution_deltas.append((resolved - created).total_seconds())

    if resolution_deltas:
        avg_secs = sum(resolution_deltas) / len(resolution_deltas)
        avg_hours = round(avg_secs / 3600, 1)
    else:
        avg_hours = None

    category_order = sorted(by_category, key=by_category.get, reverse=True)

    return render_template(
        "reports/index.html",
        total=len(tickets), open_count=open_count,
        resolved_count=resolved_count, avg_hours=avg_hours,
        status_bars=_bars(by_status, STATUSES),
        priority_bars=_bars(by_priority, PRIORITIES),
        category_bars=_bars(by_category, category_order),
        sla=sla, priorities=PRIORITIES)

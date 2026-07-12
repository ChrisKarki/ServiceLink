"""Main blueprint: homepage redirect and the role-scoped dashboard (C3.1).

FR-5.1 — role scoping:
    End User      -> only their own submitted tickets, no activity feed
    Technician    -> their assigned tickets + the unassigned team queue
    Manager       -> team-wide metrics
    Administrator -> full visibility (single-team prototype: same scope as
                     Manager; the distinction becomes real only if
                     multi-team support ever lands)

FR-5.2 — Manager/Administrator dashboards accept filters:
    days=7|30|90   tech=<userID>   priority=<Low..Critical>   status=<state>
    plus the quick-filter chips (view=open|unassigned|high|breach).
    Precedence: an explicit status filter overrides the chip.

NFR-P2 — every panel is a single aggregate query over indexed columns;
trivially inside the 3-second budget at prototype scale.

Katie (AC traceability): each panel value is produced by exactly one named
function below, and each function contains exactly one SQL statement you
can paste into the mysql client with parameters substituted. No panel
value is derived from another panel.
"""

from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, session, url_for

from ..db import query_all, query_one
from .auth import login_required

bp = Blueprint("main", __name__)

STATUS_LABELS = {
    "New": "New", "Assigned": "Assigned", "InProgress": "In Progress",
    "WaitingOnUser": "Waiting on User", "Resolved": "Resolved", "Closed": "Closed",
}

# Badge presentation copied verbatim from the Phase 3 prototype markup so
# the rendered table stays pixel-identical to Arshdeep's static page.
_MUTED = 'border: 1px solid var(--panel-border); color: var(--text-secondary);'
_WARN = 'border: 1px solid var(--warning-color); color: var(--warning-color);'
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.get("/")
def index():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.get("/dashboard")
@login_required
def dashboard():
    role = session["role"]
    uid = session["user_id"]

    if role in ("Manager", "Administrator"):
        filters = _parse_filters(request.args)
        ctx = {
            "cards": _cards_manager(filters),
            "volume": _volume(scope_sql="", scope_params=(), filters=filters),
            "feed": _activity_feed(),
            "tickets": _recent_tickets_manager(filters),
            "filters": filters,
            "technicians": _active_technicians(),
            "chips": [("open", "All Open"), ("unassigned", "Unassigned"),
                      ("high", "High Priority"), ("breach", "SLA Breach")],
        }
    elif role == "Technician":
        ctx = {
            "cards": _cards_technician(uid),
            "volume": _volume(scope_sql="", scope_params=(), filters=None),
            "feed": _activity_feed(),
            "tickets": _recent_tickets_technician(uid, request.args.get("view", "open")),
            "filters": None,
            "technicians": [],
            "chips": [("open", "My Open"), ("unassigned", "Team Queue"),
                      ("high", "High Priority"), ("breach", "SLA Breach")],
        }
    else:  # End User — FR-2.1: sees only tickets they personally submitted
        ctx = {
            "cards": _cards_enduser(uid),
            "volume": _volume(scope_sql=" AND t.submittedByUserID = %s",
                              scope_params=(uid,), filters=None),
            "feed": None,  # a global audit feed would leak other users' activity
            "tickets": _recent_tickets_enduser(uid, request.args.get("view", "open")),
            "filters": None,
            "technicians": [],
            "chips": [("open", "All Open"), ("waiting", "Waiting on Me"),
                      ("resolved", "Resolved")],
        }

    ctx["view"] = request.args.get("view", "open")
    return render_template("dashboard.html", **ctx)


# ---------------------------------------------------------------------------
# FR-5.2 filter parsing (Manager/Administrator only)
# ---------------------------------------------------------------------------

def _parse_filters(args):
    """Whitelist-validate filter params. Filter values are never
    interpolated into SQL text, only bound as parameters (NFR-S4)."""
    days = args.get("days", "30")
    days = int(days) if days in ("7", "30", "90") else 30

    tech = args.get("tech", "all")
    tech = int(tech) if tech.isdigit() else None

    priority = args.get("priority", "all")
    priority = priority if priority in PRIORITY_BADGES else None

    status = args.get("status", "all")
    status = status if status in STATUS_BADGES else None

    return {"days": days, "tech": tech, "priority": priority, "status": status,
            "view": args.get("view", "open")}


def _filter_clause(filters, alias="t"):
    """Static SQL fragments selected by whitelist; values bound as params."""
    sql, params = "", []
    if filters["tech"] is not None:
        sql += f" AND {alias}.assignedToUserID = %s"
        params.append(filters["tech"])
    if filters["priority"] is not None:
        sql += f" AND {alias}.priority = %s"
        params.append(filters["priority"])
    return sql, params


# ---------------------------------------------------------------------------
# Stat cards — one query per card
# ---------------------------------------------------------------------------

def _count(sql, params=()):
    return query_one(sql, params)["n"]


def _cards_manager(f):
    fs, fp = _filter_clause(f)
    open_n = _count(
        "SELECT COUNT(*) AS n FROM Ticket t"
        " WHERE t.status IN ('New','Assigned','InProgress','WaitingOnUser')" + fs,
        tuple(fp))
    unassigned = _count(
        "SELECT COUNT(*) AS n FROM Ticket t"
        " WHERE t.status = 'New' AND t.assignedToUserID IS NULL")
    resolved_today = _count(
        "SELECT COUNT(*) AS n FROM Ticket t"
        " WHERE t.resolvedAt >= CURDATE()" + fs, tuple(fp))
    resolved_yesterday = _count(
        "SELECT COUNT(*) AS n FROM Ticket t"
        " WHERE t.resolvedAt >= CURDATE() - INTERVAL 1 DAY"
        "   AND t.resolvedAt <  CURDATE()" + fs, tuple(fp))
    avg_res = _avg_resolution(
        " AND t.resolvedAt >= NOW() - INTERVAL %s DAY" + fs,
        (f["days"], *fp))

    delta = resolved_today - resolved_yesterday
    return [
        {"label": "Open Tickets", "value": open_n,
         "sub": "Needs attention" if open_n else "All clear", "tone": ""},
        {"label": "Team Unassigned", "value": unassigned,
         "sub": "Waiting in queue", "tone": "warning" if unassigned else "success"},
        {"label": "Resolved Today", "value": resolved_today,
         "sub": f"{delta:+d} vs yesterday", "tone": "success" if delta >= 0 else "warning"},
        {"label": "Avg Resolution Time", "value": avg_res,
         "sub": f"Last {f['days']} days", "tone": "success"},
    ]


def _cards_technician(uid):
    my_open = _count(
        "SELECT COUNT(*) AS n FROM Ticket t WHERE t.assignedToUserID = %s"
        " AND t.status IN ('Assigned','InProgress','WaitingOnUser')", (uid,))
    queue = _count(
        "SELECT COUNT(*) AS n FROM Ticket t"
        " WHERE t.status = 'New' AND t.assignedToUserID IS NULL")
    resolved_today = _count(
        "SELECT COUNT(*) AS n FROM Ticket t"
        " WHERE t.assignedToUserID = %s AND t.resolvedAt >= CURDATE()", (uid,))
    avg_res = _avg_resolution(
        " AND t.assignedToUserID = %s AND t.resolvedAt >= NOW() - INTERVAL 30 DAY",
        (uid,))
    return [
        {"label": "My Open Tickets", "value": my_open,
         "sub": "Needs attention" if my_open else "All clear", "tone": ""},
        {"label": "Team Queue", "value": queue,
         "sub": "Waiting in queue", "tone": "warning" if queue else "success"},
        {"label": "Resolved by Me Today", "value": resolved_today,
         "sub": "Today so far", "tone": "success"},
        {"label": "My Avg Resolution", "value": avg_res,
         "sub": "Last 30 days", "tone": "success"},
    ]


def _cards_enduser(uid):
    my_open = _count(
        "SELECT COUNT(*) AS n FROM Ticket t WHERE t.submittedByUserID = %s"
        " AND t.status IN ('New','Assigned','InProgress','WaitingOnUser')", (uid,))
    waiting = _count(
        "SELECT COUNT(*) AS n FROM Ticket t"
        " WHERE t.submittedByUserID = %s AND t.status = 'WaitingOnUser'", (uid,))
    resolved_30d = _count(
        "SELECT COUNT(*) AS n FROM Ticket t WHERE t.submittedByUserID = %s"
        " AND t.resolvedAt >= NOW() - INTERVAL 30 DAY", (uid,))
    avg_res = _avg_resolution(
        " AND t.submittedByUserID = %s AND t.resolvedAt >= NOW() - INTERVAL 30 DAY",
        (uid,))
    return [
        {"label": "My Open Tickets", "value": my_open,
         "sub": "Being worked on" if my_open else "All clear", "tone": ""},
        {"label": "Waiting on Me", "value": waiting,
         "sub": "Technician needs your reply", "tone": "warning" if waiting else "success"},
        {"label": "Resolved (30 days)", "value": resolved_30d,
         "sub": "Your tickets", "tone": "success"},
        {"label": "Avg Resolution Time", "value": avg_res,
         "sub": "Your tickets, last 30 days", "tone": "success"},
    ]


def _avg_resolution(scope_sql, params):
    """Derived value per Phase 3 §4.2: resolvedAt − createdAt, computed at
    query time, never stored."""
    row = query_one(
        "SELECT AVG(TIMESTAMPDIFF(MINUTE, t.createdAt, t.resolvedAt)) AS m"
        "  FROM Ticket t WHERE t.resolvedAt IS NOT NULL" + scope_sql, params)
    mins = row["m"]
    if mins is None:
        return "\u2014"
    mins = float(mins)
    if mins < 60:
        return f"{mins:.0f}m"
    if mins < 48 * 60:
        return f"{mins / 60:.1f}h"
    return f"{mins / 1440:.1f}d"


# ---------------------------------------------------------------------------
# Ticket volume — last 7 days, one bar per day
# ---------------------------------------------------------------------------

def _volume(scope_sql, scope_params, filters):
    extra_sql, extra_params = "", []
    if filters:
        extra_sql, extra_params = _filter_clause(filters)
    rows = query_all(
        "SELECT DATE(t.createdAt) AS d, COUNT(*) AS n FROM Ticket t"
        " WHERE t.createdAt >= CURDATE() - INTERVAL 6 DAY"
        + scope_sql + extra_sql +
        " GROUP BY DATE(t.createdAt)",
        (*scope_params, *extra_params))
    by_day = {r["d"]: r["n"] for r in rows}

    today = datetime.now().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    counts = [by_day.get(d, 0) for d in days]
    peak = max(counts) or 1
    return [
        {
            "label": d.strftime("%a"),
            "count": n,
            # 70% is the tallest bar in the prototype markup; 3% keeps a
            # visible sliver on zero-days so the axis doesn't look broken.
            "pct": max(round(n / peak * 70), 3),
            "is_peak": n == peak and n > 0,
        }
        for d, n in zip(days, counts)
    ]


# ---------------------------------------------------------------------------
# Activity feed — staff only, straight from the audit trail (FR-6.2 data)
# ---------------------------------------------------------------------------

_FEED_VERBS = {"Create": "created", "Update": "updated", "Delete": "deleted",
               "Link": "linked", "Unlink": "unlinked"}
_FEED_NOUNS = {"Ticket": "ticket", "Resource": "resource", "User": "user",
               "KBArticle": "article", "TicketComment": "comment",
               "TicketResource": "resource link on ticket"}


def _activity_feed():
    rows = query_all(
        "SELECT a.action, a.entityType, a.entityID, a.timestamp,"
        "       u.firstName, u.lastName"
        "  FROM AuditLog a JOIN User u ON u.userID = a.actorID"
        " ORDER BY a.timestamp DESC LIMIT 4")
    return [
        {
            "name": f"{r['firstName']} {r['lastName']}",
            "initials": (r["firstName"][:1] + r["lastName"][:1]).upper(),
            "verb": _FEED_VERBS[r["action"]],
            "target": f"{_FEED_NOUNS[r['entityType']]} #{r['entityID']}",
            "accent": r["action"] == "Create",
            "when": _age(r["timestamp"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Recent tickets table — role-scoped list + quick-filter chips
# ---------------------------------------------------------------------------

_CHIP_CLAUSES = {
    "open":       " AND t.status <> 'Closed'",
    "unassigned": " AND t.status = 'New' AND t.assignedToUserID IS NULL",
    "high":       " AND t.priority IN ('High','Critical') AND t.status <> 'Closed'",
    "breach":     " AND t.slaBreached = TRUE",
    "waiting":    " AND t.status = 'WaitingOnUser'",
    "resolved":   " AND t.status = 'Resolved'",
}

_TICKET_SELECT = (
    "SELECT t.ticketID, t.title, t.status, t.priority, t.slaBreached,"
    "       t.createdAt, t.assignedToUserID,"
    "       CONCAT(u.firstName, ' ', u.lastName) AS assignee"
    "  FROM Ticket t LEFT JOIN User u ON u.userID = t.assignedToUserID"
    " WHERE 1=1")


def _recent_tickets_manager(f):
    sql, params = _TICKET_SELECT, []
    if f["status"] is not None:
        # Explicit FR-5.2 status filter overrides the quick-filter chip
        sql += " AND t.status = %s"
        params.append(f["status"])
    else:
        sql += _CHIP_CLAUSES.get(f["view"], _CHIP_CLAUSES["open"])
    fs, fp = _filter_clause(f)
    sql += fs
    params.extend(fp)
    sql += " AND t.createdAt >= NOW() - INTERVAL %s DAY"
    params.append(f["days"])
    return _shape(query_all(sql + " ORDER BY t.createdAt DESC LIMIT 8", tuple(params)))


def _recent_tickets_technician(uid, view):
    if view == "unassigned":
        scope = " AND t.status = 'New' AND t.assignedToUserID IS NULL"
        params = ()
    else:
        scope = (" AND (t.assignedToUserID = %s"
                 "      OR (t.status = 'New' AND t.assignedToUserID IS NULL))"
                 + _CHIP_CLAUSES.get(view, _CHIP_CLAUSES["open"]))
        params = (uid,)
    return _shape(query_all(
        _TICKET_SELECT + scope + " ORDER BY t.createdAt DESC LIMIT 8", params))


def _recent_tickets_enduser(uid, view):
    scope = " AND t.submittedByUserID = %s" + _CHIP_CLAUSES.get(view, _CHIP_CLAUSES["open"])
    return _shape(query_all(
        _TICKET_SELECT + scope + " ORDER BY t.createdAt DESC LIMIT 8", (uid,)))


def _shape(rows):
    """Attach presentation data so the template stays logic-free."""
    return [
        {
            "id": r["ticketID"],
            "title": r["title"],
            "status_label": STATUS_LABELS[r["status"]],
            "status_badge": STATUS_BADGES[r["status"]],
            "priority": r["priority"],
            "priority_badge": PRIORITY_BADGES[r["priority"]],
            "assignee": r["assignee"],
            "unassigned": r["assignedToUserID"] is None,
            "age": _age(r["createdAt"]),
        }
        for r in rows
    ]


def _active_technicians():
    return query_all(
        "SELECT userID, CONCAT(firstName, ' ', lastName) AS name FROM User"
        " WHERE role = 'Technician' AND status = 'Active' ORDER BY firstName")


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

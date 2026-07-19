"""Reports (C3.1) — Manager/Administrator analytics dashboard.

Feature set:
    - Filter bar: date range (created), category, priority, technician —
      all validated server-side, all applied in one pass over the data.
    - Headline KPIs: total / open / resolved / avg resolution / SLA
      compliance %.
    - Charts (Chart.js, themed from the CSS variables at render time):
        status doughnut · priority bar · category bar ·
        12-week created-vs-resolved trend line · technician open-load
        bar · resource status doughnut.
    - Tables: SLA compliance per priority (avg actual vs target, % met),
      top requesters (user-generated demand).
    - Auto-generated insight sentences interpreting the filtered data.

Schema notes (verified 2026-07-19): Ticket stores statuses without
spaces (InProgress, WaitingOnUser) with display labels applied at
render; assignee column is assignedToUserID; Resource assignee column
is assignedUserID. Created-timestamp key is still resolved from
candidates (drift defence) and time-based features degrade gracefully
if it is absent.
"""

from datetime import datetime, timedelta

from flask import Blueprint, render_template, request

from ..db import query_all
from .auth import roles_required

bp = Blueprint("reports", __name__, url_prefix="/reports")

STATUS_KEYS = ("New", "Assigned", "InProgress", "WaitingOnUser",
               "Resolved", "Closed")
STATUS_LABELS = {"New": "New", "Assigned": "Assigned",
                 "InProgress": "In Progress",
                 "WaitingOnUser": "Waiting on User",
                 "Resolved": "Resolved", "Closed": "Closed"}
PRIORITIES = ("Critical", "High", "Medium", "Low")
OPEN_KEYS = {"New", "Assigned", "InProgress", "WaitingOnUser"}

CREATED_KEYS = ("createdAt", "submittedAt", "created_at", "dateCreated",
                "openedAt", "createdOn")
RESOLVED_KEYS = ("resolvedAt", "resolved_at", "dateResolved")
SUBMITTER_KEYS = ("submittedByUserID", "submitterID", "createdByUserID")


def _get(row, candidates):
    for k in candidates:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _norm_status(value):
    return (value or "").replace(" ", "")


def _hours(seconds):
    return round(seconds / 3600, 1)


@bp.get("/")
@roles_required("Manager", "Administrator")
def index():
    # ------------------------------------------------------------------
    # Reference data
    # ------------------------------------------------------------------
    cat_rows = query_all("SELECT categoryID, name FROM Category", ())
    cat_names = {c["categoryID"]: c["name"] for c in cat_rows}

    user_rows = query_all(
        "SELECT userID, CONCAT(firstName, ' ', lastName) AS name, role"
        "  FROM User", ())
    user_names = {u["userID"]: u["name"] for u in user_rows}
    technicians = [u for u in user_rows
                   if u["role"] in ("Technician", "Manager")]

    sla_rows = query_all("SELECT * FROM SLAPolicy", ())
    sla = {r["priority"]: r for r in sla_rows}

    # ------------------------------------------------------------------
    # Filters (whitelist-validated; never interpolated)
    # ------------------------------------------------------------------
    def _parse_date(name):
        raw = (request.args.get(name) or "").strip()
        try:
            return datetime.strptime(raw, "%Y-%m-%d") if raw else None
        except ValueError:
            return None

    f_from = _parse_date("from")
    f_to = _parse_date("to")
    f_cat = (request.args.get("category") or "").strip()
    f_cat = int(f_cat) if f_cat.isdigit() and int(f_cat) in cat_names else None
    f_priority = request.args.get("priority") or None
    f_priority = f_priority if f_priority in PRIORITIES else None
    f_tech = (request.args.get("tech") or "").strip()
    f_tech = int(f_tech) if f_tech.isdigit() else None
    filtered = any(v is not None for v in
                   (f_from, f_to, f_cat, f_priority, f_tech))

    # ------------------------------------------------------------------
    # Ticket pass
    # ------------------------------------------------------------------
    all_tickets = query_all("SELECT * FROM Ticket", ())

    tickets = []
    for t in all_tickets:
        created = _get(t, CREATED_KEYS)
        if f_from and (not isinstance(created, datetime)
                       or created < f_from):
            continue
        if f_to and (not isinstance(created, datetime)
                     or created >= f_to + timedelta(days=1)):
            continue
        if f_cat is not None and t.get("categoryID") != f_cat:
            continue
        if f_priority and t.get("priority") != f_priority:
            continue
        if f_tech is not None and t.get("assignedToUserID") != f_tech:
            continue
        tickets.append(t)

    by_status = {k: 0 for k in STATUS_KEYS}
    by_priority = {p: 0 for p in PRIORITIES}
    by_category, by_tech_open, by_submitter = {}, {}, {}
    open_count = resolved_count = 0
    res_deltas_all = []
    res_deltas_by_p = {p: [] for p in PRIORITIES}
    sla_met_by_p = {p: [0, 0] for p in PRIORITIES}  # [met, applicable]

    now = datetime.now()
    week_starts = []
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    for i in range(11, -1, -1):
        week_starts.append(monday - timedelta(weeks=i))
    created_wk = [0] * 12
    resolved_wk = [0] * 12

    def _week_index(dt):
        if not isinstance(dt, datetime):
            return None
        delta = (dt - week_starts[0]).days
        if delta < 0:
            return None
        idx = delta // 7
        return idx if 0 <= idx < 12 else None

    for t in tickets:
        skey = _norm_status(t.get("status"))
        if skey in by_status:
            by_status[skey] += 1
        priority = t.get("priority")
        if priority in by_priority:
            by_priority[priority] += 1
        cname = cat_names.get(t.get("categoryID"), "Uncategorised")
        by_category[cname] = by_category.get(cname, 0) + 1

        if skey in OPEN_KEYS:
            open_count += 1
            tech = t.get("assignedToUserID")
            if tech is not None:
                by_tech_open[tech] = by_tech_open.get(tech, 0) + 1
        elif skey in ("Resolved", "Closed"):
            resolved_count += 1

        sub = _get(t, SUBMITTER_KEYS)
        if sub is not None:
            by_submitter[sub] = by_submitter.get(sub, 0) + 1

        created = _get(t, CREATED_KEYS)
        resolved = _get(t, RESOLVED_KEYS)
        wi = _week_index(created)
        if wi is not None:
            created_wk[wi] += 1
        wr = _week_index(resolved)
        if wr is not None:
            resolved_wk[wr] += 1

        if (isinstance(created, datetime) and isinstance(resolved, datetime)
                and resolved >= created):
            secs = (resolved - created).total_seconds()
            res_deltas_all.append(secs)
            if priority in res_deltas_by_p:
                res_deltas_by_p[priority].append(secs)
                policy = sla.get(priority)
                if policy and policy.get("resolutionTargetMins"):
                    sla_met_by_p[priority][1] += 1
                    if secs <= policy["resolutionTargetMins"] * 60:
                        sla_met_by_p[priority][0] += 1

    avg_hours = _hours(sum(res_deltas_all) / len(res_deltas_all)) \
        if res_deltas_all else None

    sla_table = []
    met_total = applicable_total = 0
    for p in PRIORITIES:
        met, applicable = sla_met_by_p[p]
        met_total += met
        applicable_total += applicable
        deltas = res_deltas_by_p[p]
        avg_p = _hours(sum(deltas) / len(deltas)) if deltas else None
        policy = sla.get(p)
        target_h = round(policy["resolutionTargetMins"] / 60, 1) \
            if policy and policy.get("resolutionTargetMins") else None
        sla_table.append({
            "priority": p, "tickets": by_priority[p],
            "avg_hours": avg_p, "target_hours": target_h,
            "pct_met": round(met * 100 / applicable) if applicable else None,
        })
    sla_pct = round(met_total * 100 / applicable_total) \
        if applicable_total else None

    tech_labels, tech_counts = [], []
    for uid, n in sorted(by_tech_open.items(), key=lambda x: -x[1]):
        tech_labels.append(user_names.get(uid, f"User #{uid}"))
        tech_counts.append(n)

    top_submitters = [
        {"name": user_names.get(uid, f"User #{uid}"), "count": n,
         "pct": round(n * 100 / (len(tickets) or 1))}
        for uid, n in sorted(by_submitter.items(), key=lambda x: -x[1])[:5]]

    category_order = sorted(by_category, key=by_category.get, reverse=True)

    # ------------------------------------------------------------------
    # Resources (unfiltered — separate domain from the ticket filters)
    # ------------------------------------------------------------------
    resources = query_all("SELECT * FROM Resource", ())
    res_by_status, res_by_type = {}, {}
    res_assigned = 0
    for r in resources:
        res_by_status[r.get("status") or "Unknown"] = \
            res_by_status.get(r.get("status") or "Unknown", 0) + 1
        res_by_type[r.get("type") or "Other"] = \
            res_by_type.get(r.get("type") or "Other", 0) + 1
        if r.get("assignedUserID") is not None:
            res_assigned += 1

    # ------------------------------------------------------------------
    # Auto-generated interpretation
    # ------------------------------------------------------------------
    insights = []
    n = len(tickets)
    if n == 0:
        insights.append("No tickets match the current filters — widen the "
                        "date range or clear a filter to see data.")
    else:
        if category_order:
            top_c = category_order[0]
            share = round(by_category[top_c] * 100 / n)
            if share >= 25:
                insights.append(
                    f"{top_c} is the biggest demand driver at {share}% of "
                    f"tickets ({by_category[top_c]} of {n}) — a candidate "
                    "for a knowledge-base article or preventive fix.")
        backlog_pct = round(open_count * 100 / n)
        insights.append(
            f"{open_count} of {n} tickets ({backlog_pct}%) are currently "
            "open." + (" Backlog is the majority of volume — review "
                       "assignment load below." if backlog_pct > 50 else ""))
        if sla_pct is not None:
            worst = min((r for r in sla_table if r["pct_met"] is not None),
                        key=lambda r: r["pct_met"], default=None)
            line = (f"Overall SLA resolution compliance is {sla_pct}%.")
            if worst and worst["pct_met"] < 100:
                line += (f" {worst['priority']} is the weakest tier at "
                         f"{worst['pct_met']}% within target.")
            insights.append(line)
        if len(res_deltas_all) >= 1 and avg_hours is not None:
            insights.append(
                f"Average time to resolution is {avg_hours} hours across "
                f"{len(res_deltas_all)} resolved tickets.")
        if tech_labels:
            spread = (f"Open work is spread across {len(tech_labels)} "
                      f"technicians; {tech_labels[0]} carries the most "
                      f"({tech_counts[0]}).")
            if len(tech_counts) > 1 and tech_counts[0] >= 2 * tech_counts[-1]:
                spread += (" Load is uneven — consider rebalancing before "
                           "round-robin widens the gap.")
            insights.append(spread)
        unassigned = open_count - sum(tech_counts)
        if unassigned > 0:
            insights.append(f"{unassigned} open ticket"
                            f"{'' if unassigned == 1 else 's'} "
                            "have no assigned technician.")
        if resources:
            insights.append(
                f"{res_assigned} of {len(resources)} resources "
                f"({round(res_assigned * 100 / len(resources))}%) are "
                "assigned to users.")

    chart_data = {
        "status": {"labels": [STATUS_LABELS[k] for k in STATUS_KEYS],
                   "counts": [by_status[k] for k in STATUS_KEYS]},
        "priority": {"labels": list(PRIORITIES),
                     "counts": [by_priority[p] for p in PRIORITIES]},
        "category": {"labels": category_order,
                     "counts": [by_category[c] for c in category_order]},
        "trend": {"labels": [w.strftime("%b %d") for w in week_starts],
                  "created": created_wk, "resolved": resolved_wk},
        "tech": {"labels": tech_labels, "counts": tech_counts},
        "resource": {"labels": list(res_by_status.keys()),
                     "counts": list(res_by_status.values())},
        "resource_type": {"labels": list(res_by_type.keys()),
                          "counts": list(res_by_type.values())},
    }

    return render_template(
        "reports/index.html",
        total=n, open_count=open_count, resolved_count=resolved_count,
        avg_hours=avg_hours, sla_pct=sla_pct,
        sla_table=sla_table, top_submitters=top_submitters,
        insights=insights, chart_data=chart_data,
        categories=cat_rows, technicians=technicians,
        priorities=PRIORITIES, filtered=filtered,
        f_from=request.args.get("from", ""), f_to=request.args.get("to", ""),
        f_cat=f_cat, f_priority=f_priority, f_tech=f_tech,
        resource_total=len(resources))

"""Global search blueprint — cross-module search from the topbar.

Replaces the previous behaviour where the topbar form posted directly to
tickets.list_tickets, so a search issued from the Knowledge Base or the
Resources module silently returned ticket results only.

This module does not invent its own matching rules. Each section reuses the
predicate already shipped by the owning blueprint, so a search from the
topbar and a search from inside a module return the same rows:

    Articles  -> kb._scope_clause() plus the title/body/tag predicate and the
                 title-hits-first ordering from kb.list_articles (FR-4.2)
    Resources -> the resourceTag/make/model/serialNumber/location predicate
                 from resources._build_filters
    Tickets   -> title or exact ticketID, scoped per FR-5.1
    Users     -> the name/email/role predicate from resources.search_users

Access (FR-3.2 / FR-4.1 / FR-5.1), enforced in SQL, never in the template:
    End User      -> Tickets (own submissions only), Articles (Published +
                     Public only, the same rule as kb._scope_clause)
    Technician    -> the above, plus Resources, plus their own draft articles
    Manager       -> the above, plus the user directory
    Administrator -> same as Manager (single-team prototype)

NFR-S4: the query string is bound as a parameter in every statement. The
only values interpolated into SQL text are the integer row limits defined
as module constants below.

Katie (AC traceability): one named count function and one named finder per
section, one SQL statement each. No section count is derived from another.
"""

from flask import Blueprint, render_template, request, session, url_for

from ..db import query_all, query_one
from . import kb as kb_mod
from . import main as main_mod
from . import resources as res_mod
from .auth import login_required

bp = Blueprint("search", __name__, url_prefix="/search")

# Rows per section. Interpolated into SQL text as integers only — never
# user-supplied, so NFR-S4 is unaffected.
LIMIT_GROUPED = 5    # "All" tab: a preview of each section
LIMIT_SINGLE = 25    # a single-section tab

MIN_QUERY_LEN = 2

SECTIONS = ("tickets", "resources", "articles", "users")
SECTION_LABELS = {"tickets": "Tickets", "resources": "Resources",
                  "articles": "Articles", "users": "Users"}

_MUTED = ("border: 1px solid var(--panel-border);"
          " color: var(--text-secondary);")
_WARN = ("border: 1px solid var(--warning-color);"
         " color: var(--warning-color);")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@bp.get("/")
@login_required
def index():
    role = session["role"]
    uid = session["user_id"]

    q = (request.args.get("q") or "").strip()
    scope = request.args.get("type", "all")
    if scope not in SECTIONS:
        scope = "all"

    allowed = _allowed_sections(role)
    if scope != "all" and scope not in allowed:
        scope = "all"

    ctx = {
        "q": q,
        "scope": scope,
        "sections": allowed,
        "labels": SECTION_LABELS,
        "counts": {s: 0 for s in allowed},
        "results": {s: [] for s in allowed},
        "total": 0,
        "too_short": bool(q) and len(q) < MIN_QUERY_LEN,
    }

    if not q or ctx["too_short"]:
        return render_template("search.html", **ctx)

    limit = LIMIT_SINGLE if scope != "all" else LIMIT_GROUPED

    for name in allowed:
        counter, finder = _DISPATCH[name]
        ctx["counts"][name] = counter(q, uid, role)
        if scope in ("all", name):
            ctx["results"][name] = finder(q, uid, role, limit)

    ctx["total"] = sum(ctx["counts"].values())
    return render_template("search.html", **ctx)


def _allowed_sections(role):
    """Mirrors the sidebar visibility rules and the @roles_required guards on
    the owning blueprints. A section a role cannot reach is not merely
    hidden — its queries are never run."""
    if role == "EndUser":
        return ("tickets", "articles")
    if role == "Technician":
        return ("tickets", "resources", "articles")
    return SECTIONS  # Manager / Administrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _like(q):
    """LIKE pattern with wildcards escaped, so a literal % or _ typed by the
    user is matched as typed rather than acting as a wildcard."""
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _as_id(q):
    """Exact-ID match arm. Returns a sentinel matching no row when the query
    is not numeric, so the SQL text stays constant."""
    return int(q) if q.isdigit() else -1


def _n(sql, params):
    return query_one(sql, params)["n"]


# ---------------------------------------------------------------------------
# Tickets — FR-5.1 scoping
# ---------------------------------------------------------------------------

_TICKET_WHERE = " WHERE (t.title LIKE %s OR t.ticketID = %s)"


def _ticket_scope(role):
    """End Users see only tickets they personally submitted — the same rule
    main._recent_tickets_enduser applies on the dashboard."""
    if role == "EndUser":
        return " AND t.submittedByUserID = %s", True
    return "", False


def _count_tickets(q, uid, role):
    scope_sql, needs_uid = _ticket_scope(role)
    params = [_like(q), _as_id(q)] + ([uid] if needs_uid else [])
    return _n("SELECT COUNT(*) AS n FROM Ticket t" + _TICKET_WHERE + scope_sql,
              tuple(params))


def _find_tickets(q, uid, role, limit):
    scope_sql, needs_uid = _ticket_scope(role)
    params = [_like(q), _as_id(q)] + ([uid] if needs_uid else [])
    rows = query_all(
        "SELECT t.ticketID, t.title, t.status, t.priority, t.createdAt,"
        "       t.assignedToUserID,"
        "       CONCAT(u.firstName, ' ', u.lastName) AS assignee"
        "  FROM Ticket t LEFT JOIN User u ON u.userID = t.assignedToUserID"
        + _TICKET_WHERE + scope_sql +
        f" ORDER BY t.createdAt DESC LIMIT {int(limit)}",
        tuple(params))
    return [
        {
            "ref": f"#{r['ticketID']}",
            "title": r["title"],
            "url": url_for("tickets.view_ticket", ticket_id=r["ticketID"]),
            "badges": [
                {"text": main_mod.STATUS_LABELS[r["status"]],
                 **main_mod.STATUS_BADGES[r["status"]]},
                {"text": r["priority"],
                 **main_mod.PRIORITY_BADGES[r["priority"]]},
            ],
            "meta": r["assignee"] or "Unassigned",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Resources — staff only (FR-3.2). Predicate copied from
# resources._build_filters so topbar and in-module search agree.
# ---------------------------------------------------------------------------

_RESOURCE_WHERE = (
    " WHERE (r.resourceTag LIKE %s OR r.make LIKE %s OR r.model LIKE %s"
    "        OR r.serialNumber LIKE %s OR r.location LIKE %s"
    "        OR r.resourceID = %s)")


def _resource_params(q):
    return tuple([_like(q)] * 5 + [_as_id(q)])


def _count_resources(q, uid, role):
    return _n("SELECT COUNT(*) AS n FROM Resource r" + _RESOURCE_WHERE,
              _resource_params(q))


def _find_resources(q, uid, role, limit):
    rows = query_all(
        "SELECT r.resourceID, r.resourceTag, r.type, r.make, r.model,"
        "       r.serialNumber, r.status, r.location,"
        "       CONCAT(u.firstName, ' ', u.lastName) AS assignedName"
        "  FROM Resource r LEFT JOIN User u ON u.userID = r.assignedUserID"
        + _RESOURCE_WHERE +
        f" ORDER BY r.resourceTag LIMIT {int(limit)}",
        _resource_params(q))
    return [
        {
            "ref": r["resourceTag"],
            "title": (" ".join(x for x in (r["make"], r["model"]) if x)
                      or r["type"]),
            "url": url_for("resources.view_resource",
                           resource_id=r["resourceID"]),
            "badges": [
                {"text": res_mod.STATUS_LABELS[r["status"]],
                 **res_mod.STATUS_BADGES[r["status"]]},
                {"text": r["type"], "cls": "badge", "style": _MUTED},
            ],
            "meta": " · ".join(x for x in (
                r["serialNumber"], r["location"],
                (f"Used by {r['assignedName']}" if r["assignedName"] else None),
            ) if x),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Knowledge Base — FR-4.2. Reuses kb._scope_clause() verbatim, so an End User
# hitting this route can never surface a Draft, PendingApproval, or Internal
# article. Closes TC-04 AF-2.
# ---------------------------------------------------------------------------

_ARTICLE_MATCH = (
    " AND (a.title LIKE %s OR a.body LIKE %s OR EXISTS"
    "      (SELECT 1 FROM ArticleTag t"
    "        WHERE t.articleID = a.articleID AND t.tag LIKE %s)"
    "      OR a.articleID = %s)")


def _article_where(q):
    scope_sql, params = kb_mod._scope_clause()
    params = list(params) + [_like(q)] * 3 + [_as_id(q)]
    return " WHERE 1=1" + scope_sql + _ARTICLE_MATCH, params


def _count_articles(q, uid, role):
    where, params = _article_where(q)
    return _n("SELECT COUNT(*) AS n FROM KBArticle a" + where, tuple(params))


def _find_articles(q, uid, role, limit):
    where, params = _article_where(q)
    # Title hits rank first, matching kb.list_articles ordering (NFR-P3).
    params.append(_like(q))
    rows = query_all(
        "SELECT a.articleID, a.title, a.status, a.visibility,"
        "       LEFT(a.body, 180) AS snippet,"
        "       CONCAT(au.firstName, ' ', au.lastName) AS authorName"
        "  FROM KBArticle a JOIN User au ON au.userID = a.authorID"
        + where +
        " ORDER BY (a.title LIKE %s) DESC,"
        " COALESCE(a.publishedAt, a.createdAt) DESC"
        f" LIMIT {int(limit)}",
        tuple(params))
    return [
        {
            "ref": f"KB-{r['articleID']}",
            "title": r["title"],
            "url": url_for("kb.view_article", article_id=r["articleID"]),
            "badges": [
                {"text": kb_mod.STATUS_LABELS[r["status"]],
                 **kb_mod.STATUS_BADGES[r["status"]]},
                {"text": r["visibility"],
                 **kb_mod.VISIBILITY_BADGES[r["visibility"]]},
            ],
            "meta": (r["snippet"] or "").replace("\n", " ").strip(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Users — Manager / Administrator only. Predicate copied from
# resources.search_users, minus its Active-only restriction: directory search
# must be able to find a Suspended or Inactive account.
# ---------------------------------------------------------------------------

_USER_WHERE = (
    " WHERE (CONCAT(u.firstName, ' ', u.lastName) LIKE %s"
    "        OR u.email LIKE %s OR u.role LIKE %s OR u.userID = %s)")


def _user_params(q):
    return tuple([_like(q)] * 3 + [_as_id(q)])


def _count_users(q, uid, role):
    return _n("SELECT COUNT(*) AS n FROM User u" + _USER_WHERE,
              _user_params(q))


def _find_users(q, uid, role, limit):
    rows = query_all(
        "SELECT u.userID, u.firstName, u.lastName, u.role, u.status, u.email"
        "  FROM User u" + _USER_WHERE +
        f" ORDER BY u.firstName, u.lastName LIMIT {int(limit)}",
        _user_params(q))
    return [
        {
            "ref": f"#{r['userID']}",
            "title": f"{r['firstName']} {r['lastName']}",
            "url": url_for("admin.list_users",
                           q=f"{r['firstName']} {r['lastName']}"),
            "badges": [
                {"text": r["role"], "cls": "badge", "style": _MUTED},
                {"text": r["status"],
                 "cls": ("badge badge-status-resolved"
                         if r["status"] == "Active" else "badge"),
                 "style": ("" if r["status"] == "Active" else _WARN)},
            ],
            "meta": r["email"] or "",
        }
        for r in rows
    ]


_DISPATCH = {
    "tickets":   (_count_tickets, _find_tickets),
    "resources": (_count_resources, _find_resources),
    "articles":  (_count_articles, _find_articles),
    "users":     (_count_users, _find_users),
}

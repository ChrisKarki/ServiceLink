"""Global search blueprint — cross-module search from the topbar.

Replaces the previous behaviour where the topbar form posted directly to
tickets.list_tickets, so a search issued from the Knowledge Base or the
Resources module silently returned ticket results only.

Role scoping (FR-5.1) is enforced in SQL, not in the template:
    End User      -> own submitted tickets; externally-visible KB articles
    Technician    -> all tickets, all resources, internal + external articles
    Manager       -> the above, plus the user directory
    Administrator -> same as Manager (single-team prototype)

NFR-S4: the query string is bound as a parameter in every statement. The
only values interpolated into SQL text are integer row limits defined as
module constants below.

Katie (AC traceability): one named function per result section, one SQL
statement per function, plus one COUNT statement per section for the tab
badges. No section count is derived from another section.
"""

from flask import Blueprint, render_template, request, session, url_for
from werkzeug.routing import BuildError

from ..db import query_all, query_one
from .auth import login_required
from .main import PRIORITY_BADGES, STATUS_BADGES, STATUS_LABELS

bp = Blueprint("search", __name__, url_prefix="/search")

# Rows shown per section. Interpolated into SQL text as integers only —
# never user-supplied, so NFR-S4 is unaffected.
LIMIT_GROUPED = 5    # "All" tab: a preview of each section
LIMIT_SINGLE = 25    # a single section tab

MIN_QUERY_LEN = 2

# ---------------------------------------------------------------------------
# SCHEMA BINDING — verify against the deployed schema before first run:
#     DESCRIBE Resource;  DESCRIBE KBArticle;  DESCRIBE User;
# Every column name this module touches outside Ticket/User core columns is
# declared here so a schema mismatch is a one-line edit, not a hunt.
# ---------------------------------------------------------------------------
R_TABLE, R_PK = "Resource", "resourceID"
R_NAME, R_TAG, R_TYPE, R_STATUS = "name", "assetTag", "type", "status"

K_TABLE, K_PK = "KBArticle", "articleID"
K_TITLE, K_BODY, K_VIS = "title", "content", "visibility"
K_PUBLIC = ("External",)                       # what an End User may see
K_STAFF = ("External", "Internal", "Draft")    # what staff may see

U_EMAIL = "email"

# Result link targets. Resolved through _safe_url() so a renamed endpoint
# degrades to plain text instead of raising BuildError on every render.
EP_TICKET = "tickets.ticket_detail"
EP_RESOURCE = "resources.resource_detail"
EP_ARTICLE = "kb.view_article"
EP_USER = "admin.list_users"

SECTIONS = ("tickets", "resources", "articles", "users")


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
        "counts": {s: 0 for s in allowed},
        "results": {s: [] for s in allowed},
        "total": 0,
        "too_short": bool(q) and len(q) < MIN_QUERY_LEN,
        "degraded": [],
    }

    if not q or ctx["too_short"]:
        return render_template("search.html", **ctx)

    limit = LIMIT_SINGLE if scope != "all" else LIMIT_GROUPED

    for name in allowed:
        counter, finder = _DISPATCH[name]
        try:
            ctx["counts"][name] = counter(q, uid, role)
            if scope in ("all", name):
                ctx["results"][name] = finder(q, uid, role, limit)
        except Exception:
            # One broken index must not 500 the whole search page. The
            # section is reported as unavailable and the rest still renders.
            ctx["degraded"].append(name)

    ctx["total"] = sum(ctx["counts"].values())
    return render_template("search.html", **ctx)


def _allowed_sections(role):
    if role == "EndUser":
        return ("tickets", "articles")
    if role == "Technician":
        return ("tickets", "resources", "articles")
    return SECTIONS  # Manager / Administrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _like(q):
    """Escape LIKE wildcards so a literal % or _ is matched as typed."""
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _as_id(q):
    """Exact-ID match arm. Returns a sentinel that matches no row when the
    query is not numeric, so the SQL text stays constant."""
    return int(q) if q.isdigit() else -1


def _safe_url(endpoint, **values):
    try:
        return url_for(endpoint, **values)
    except BuildError:
        return None


def _n(sql, params):
    return query_one(sql, params)["n"]


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

def _ticket_scope(role):
    return (" AND t.submittedByUserID = %s", True) if role == "EndUser" else ("", False)


def _count_tickets(q, uid, role):
    scope_sql, needs_uid = _ticket_scope(role)
    params = [_like(q), _as_id(q)] + ([uid] if needs_uid else [])
    return _n(
        "SELECT COUNT(*) AS n FROM Ticket t"
        " WHERE (t.title LIKE %s OR t.ticketID = %s)" + scope_sql,
        tuple(params))


def _find_tickets(q, uid, role, limit):
    scope_sql, needs_uid = _ticket_scope(role)
    params = [_like(q), _as_id(q)] + ([uid] if needs_uid else [])
    rows = query_all(
        "SELECT t.ticketID, t.title, t.status, t.priority, t.createdAt,"
        "       t.assignedToUserID,"
        "       CONCAT(u.firstName, ' ', u.lastName) AS assignee"
        "  FROM Ticket t LEFT JOIN User u ON u.userID = t.assignedToUserID"
        " WHERE (t.title LIKE %s OR t.ticketID = %s)" + scope_sql +
        f" ORDER BY t.createdAt DESC LIMIT {int(limit)}",
        tuple(params))
    return [
        {
            "ref": f"#{r['ticketID']}",
            "title": r["title"],
            "url": _safe_url(EP_TICKET, ticket_id=r["ticketID"]),
            "badges": [
                {"text": STATUS_LABELS[r["status"]], **STATUS_BADGES[r["status"]]},
                {"text": r["priority"], **PRIORITY_BADGES[r["priority"]]},
            ],
            "meta": r["assignee"] or "Unassigned",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Resources — staff only (matches the sidebar visibility rule)
# ---------------------------------------------------------------------------

def _count_resources(q, uid, role):
    return _n(
        f"SELECT COUNT(*) AS n FROM {R_TABLE} r"
        f" WHERE (r.{R_NAME} LIKE %s OR r.{R_TAG} LIKE %s OR r.{R_PK} = %s)",
        (_like(q), _like(q), _as_id(q)))


def _find_resources(q, uid, role, limit):
    rows = query_all(
        f"SELECT r.{R_PK} AS id, r.{R_NAME} AS name, r.{R_TAG} AS tag,"
        f"       r.{R_TYPE} AS type, r.{R_STATUS} AS status"
        f"  FROM {R_TABLE} r"
        f" WHERE (r.{R_NAME} LIKE %s OR r.{R_TAG} LIKE %s OR r.{R_PK} = %s)"
        f" ORDER BY r.{R_NAME} LIMIT {int(limit)}",
        (_like(q), _like(q), _as_id(q)))
    return [
        {
            "ref": r["tag"] or f"#{r['id']}",
            "title": r["name"],
            "url": _safe_url(EP_RESOURCE, resource_id=r["id"]),
            "badges": [{"text": r["status"], "cls": "badge", "style": ""}],
            "meta": r["type"] or "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Knowledge Base articles
# ---------------------------------------------------------------------------

def _kb_visibility(role):
    """Returns (sql_fragment, params). End Users never see Draft or
    Internal articles — enforced here, not in the template."""
    allowed = K_PUBLIC if role == "EndUser" else K_STAFF
    placeholders = ", ".join(["%s"] * len(allowed))
    return f" AND a.{K_VIS} IN ({placeholders})", list(allowed)


def _count_articles(q, uid, role):
    vis_sql, vis_params = _kb_visibility(role)
    return _n(
        f"SELECT COUNT(*) AS n FROM {K_TABLE} a"
        f" WHERE (a.{K_TITLE} LIKE %s OR a.{K_BODY} LIKE %s OR a.{K_PK} = %s)"
        + vis_sql,
        (_like(q), _like(q), _as_id(q), *vis_params))


def _find_articles(q, uid, role, limit):
    vis_sql, vis_params = _kb_visibility(role)
    rows = query_all(
        f"SELECT a.{K_PK} AS id, a.{K_TITLE} AS title, a.{K_VIS} AS visibility,"
        f"       LEFT(a.{K_BODY}, 180) AS snippet"
        f"  FROM {K_TABLE} a"
        f" WHERE (a.{K_TITLE} LIKE %s OR a.{K_BODY} LIKE %s OR a.{K_PK} = %s)"
        + vis_sql +
        f" ORDER BY a.{K_TITLE} LIMIT {int(limit)}",
        (_like(q), _like(q), _as_id(q), *vis_params))
    return [
        {
            "ref": f"KB-{r['id']}",
            "title": r["title"],
            "url": _safe_url(EP_ARTICLE, article_id=r["id"]),
            "badges": [{"text": r["visibility"], "cls": "badge", "style": ""}],
            "meta": (r["snippet"] or "").replace("\n", " ").strip(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Users — Manager / Administrator only
# ---------------------------------------------------------------------------

def _count_users(q, uid, role):
    return _n(
        "SELECT COUNT(*) AS n FROM User u"
        " WHERE (CONCAT(u.firstName, ' ', u.lastName) LIKE %s"
        f"        OR u.{U_EMAIL} LIKE %s OR u.userID = %s)",
        (_like(q), _like(q), _as_id(q)))


def _find_users(q, uid, role, limit):
    rows = query_all(
        "SELECT u.userID, u.firstName, u.lastName, u.role, u.status,"
        f"       u.{U_EMAIL} AS email"
        "  FROM User u"
        " WHERE (CONCAT(u.firstName, ' ', u.lastName) LIKE %s"
        f"        OR u.{U_EMAIL} LIKE %s OR u.userID = %s)"
        f" ORDER BY u.firstName, u.lastName LIMIT {int(limit)}",
        (_like(q), _like(q), _as_id(q)))
    return [
        {
            "ref": f"#{r['userID']}",
            "title": f"{r['firstName']} {r['lastName']}",
            "url": _safe_url(EP_USER),
            "badges": [
                {"text": r["role"], "cls": "badge", "style": ""},
                {"text": r["status"], "cls": "badge", "style": ""},
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

SECTION_LABELS = {
    "tickets": "Tickets", "resources": "Resources",
    "articles": "Articles", "users": "Users",
}


@bp.app_context_processor
def _inject_section_labels():
    return {"search_section_labels": SECTION_LABELS}

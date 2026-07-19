"""Knowledge Base blueprint — feature/kb (FR-4.1, FR-4.2, UC-04, NFR-P3).

ALIGNED TO THE DEPLOYED SCHEMA (verified via DESCRIBE, 2026-07-19):
  KBArticle(articleID, title, body, authorID, approvedByID,
            status ENUM('Draft','PendingApproval','Published','Archived'),
            visibility ENUM('Internal','Public'), createdAt, publishedAt)
  Tag table is named ArticleTag. Ticket.linkedKBArticleID exists.
  There is NO categoryID, viewCount, or updatedAt on KBArticle — the
  category filter, view counter, and updated-ordering from the first cut
  are removed rather than faked. 'Archived' exists in the ENUM; this
  module displays it but exposes no archive action (out of card scope).

  [Guessing — verify with `DESCRIBE ArticleTag;` before first run]:
  tag queries assume ArticleTag(articleID, tag). If the real columns
  differ, only _sync_tags, the two tag EXISTS subqueries, and the
  view-page tag SELECT need touching.

Implemented here:
  FR-4.1  Draft → PendingApproval → Published workflow.
          Technicians author drafts and submit them; Managers/Administrators
          approve (publish) or return to Draft. A Technician-author editing
          a Published article sends it back to PendingApproval — published
          content is always Manager-approved content.
  FR-4.1  Visibility Internal/Public — enforced in SQL via _scope_clause,
          never in templates. End Users can only ever SELECT rows that are
          Published AND Public.
  FR-4.1  Tag CRUD — tags are synced from a comma-separated field on every
          save (create/update/delete in one pass), capped and length-checked.
  FR-4.2  Search over title/body/tags, role-scoped, title matches ranked
          first (NFR-P3: single LIMIT-bounded query).
  FR-4.2  Ticket integration — JSON article search for the ticket detail
          modal, and POST /tickets/<id>/kb-article writes
          Ticket.linkedKBArticleID (the resolution article). The
          ticket-detail template seam is Prabh's — see
          patches/ticket_detail_kb_snippet.html.

Route map (no url_prefix, matching the tickets blueprint convention):
  GET       /kb                        list + chips + search
  GET/POST  /kb/new                    create (Draft, or straight to Pending)
  GET       /kb/<id>                   view (scoped)
  GET/POST  /kb/<id>/edit              edit (author or Manager/Admin)
  POST      /kb/<id>/submit            author: Draft → PendingApproval
  POST      /kb/<id>/approve           Mgr/Admin: PendingApproval → Published
  POST      /kb/<id>/return            Mgr/Admin: PendingApproval → Draft
  GET       /kb/search.json            role-scoped JSON search (ticket modal)
  POST      /tickets/<id>/kb-article   set/clear Ticket.linkedKBArticleID

All mutations go through services.audit.log_action with ip=request.remote_addr
(C0.1). Every user-supplied value is parameter-bound (NFR-S4). Denials are
abort(403), matching roles_required.
"""

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, session, url_for)

from ..db import execute, query_all, query_one
from ..services.audit import diff_fields, log_action
from ..services.notify import send as notify
from .auth import login_required, roles_required
from .tickets import _get_ticket_or_403

bp = Blueprint("kb", __name__)

STAFF = ("Technician", "Manager", "Administrator")
APPROVERS = ("Manager", "Administrator")

# Workflow states this module drives. 'Archived' exists in the deployed ENUM
# and is displayed if present, but no route sets it (out of card scope).
STATUSES = ("Draft", "PendingApproval", "Published")
STATUS_LABELS = {"Draft": "Draft", "PendingApproval": "Pending Approval",
                 "Published": "Published", "Archived": "Archived"}
VISIBILITIES = ("Internal", "Public")

MAX_TAGS = 10
MAX_TAG_LEN = 40

# Badge presentation, same convention as tickets/resources _shape.
_MUTED = "border: 1px solid var(--panel-border); color: var(--text-secondary);"
_WARN = "border: 1px solid var(--warning-color); color: var(--warning-color);"
STATUS_BADGES = {
    "Draft":           {"cls": "badge", "style": _MUTED},
    "PendingApproval": {"cls": "badge", "style": _WARN},
    "Published":       {"cls": "badge badge-status-resolved", "style": ""},
    "Archived":        {"cls": "badge", "style": _MUTED},
}
VISIBILITY_BADGES = {
    "Public":   {"cls": "badge badge-status-open", "style": ""},
    "Internal": {"cls": "badge", "style": _WARN},
}

# Deployed columns: authorID / approvedByID (NOT ...UserID), no Category join.
_ARTICLE_SELECT = (
    "SELECT a.*,"
    "       CONCAT(au.firstName, ' ', au.lastName) AS authorName,"
    "       CONCAT(ap.firstName, ' ', ap.lastName) AS approverName"
    "  FROM KBArticle a"
    "  JOIN User au    ON au.userID = a.authorID"
    "  LEFT JOIN User ap ON ap.userID = a.approvedByID")


# ---------------------------------------------------------------------------
# Role scoping — the single source of truth for who may see which article
# ---------------------------------------------------------------------------

def _scope_clause():
    """SQL fragment + params restricting article rows to the session role.

    EndUser:    Published AND Public only — the FR-4.1 acceptance criterion.
    Technician: Published (any visibility) plus their OWN articles in any
                state. Other technicians' drafts stay private to the
                author + approvers.
    Manager / Administrator: everything.
    """
    role, uid = session["role"], session["user_id"]
    if role == "EndUser":
        return " AND a.status = 'Published' AND a.visibility = 'Public'", []
    if role == "Technician":
        return " AND (a.status = 'Published' OR a.authorID = %s)", [uid]
    return "", []


def _get_article_or_403(article_id):
    """Fetch one article and enforce the same scope as the list/search SQL.
    404 if it doesn't exist, 403 if this role/user may not see it — hard
    denial, mirroring tickets._get_ticket_or_403."""
    a = query_one(_ARTICLE_SELECT + " WHERE a.articleID = %s", (article_id,))
    if a is None:
        abort(404)
    role, uid = session["role"], session["user_id"]
    if role == "EndUser" and not (
            a["status"] == "Published" and a["visibility"] == "Public"):
        abort(403)
    if role == "Technician" and not (
            a["status"] == "Published" or a["authorID"] == uid):
        abort(403)
    return a


def _can_edit(a):
    """Author may edit their own article; approvers may edit any."""
    return (session["role"] in APPROVERS
            or a["authorID"] == session["user_id"])


# ---------------------------------------------------------------------------
# List + search (FR-4.2)
# ---------------------------------------------------------------------------

_CHIPS_STAFF = [("all", "All"), ("published", "Published"),
                ("pending", "Pending Approval"), ("mine", "My Articles"),
                ("internal", "Internal")]

_CHIP_CLAUSES = {
    "all":       "",
    "published": " AND a.status = 'Published'",
    "pending":   " AND a.status = 'PendingApproval'",
    "internal":  " AND a.visibility = 'Internal' AND a.status = 'Published'",
    # "mine" binds a param — handled inline below.
}


@bp.get("/kb")
@login_required
def list_articles():
    role = session["role"]
    view = request.args.get("view", "all")
    view = view if role != "EndUser" and view in (
        list(_CHIP_CLAUSES) + ["mine"]) else "all"
    q = (request.args.get("q") or "").strip()

    scope_sql, params = _scope_clause()
    sql = _ARTICLE_SELECT + " WHERE 1=1" + scope_sql

    if view == "mine":
        sql += " AND a.authorID = %s"
        params.append(session["user_id"])
    else:
        sql += _CHIP_CLAUSES.get(view, "")

    # No updatedAt on the deployed table — newest-published first, then
    # newest-created (covers drafts/pending which have no publishedAt).
    order = " ORDER BY COALESCE(a.publishedAt, a.createdAt) DESC"
    if q:
        # FR-4.2: title/body/tag search; title hits rank first (NFR-P3).
        sql += (" AND (a.title LIKE %s OR a.body LIKE %s OR EXISTS"
                "      (SELECT 1 FROM ArticleTag t"
                "        WHERE t.articleID = a.articleID AND t.tag LIKE %s))")
        params.extend([f"%{q}%"] * 3)
        order = (" ORDER BY (a.title LIKE %s) DESC,"
                 " COALESCE(a.publishedAt, a.createdAt) DESC")
        params.append(f"%{q}%")

    rows = query_all(sql + order + " LIMIT 100", tuple(params))

    return render_template(
        "kb/list.html",
        articles=[_shape(a) for a in rows],
        chips=_CHIPS_STAFF if role != "EndUser" else [],
        view=view, q=q,
        pending_count=(query_one(
            "SELECT COUNT(*) AS n FROM KBArticle"
            " WHERE status = 'PendingApproval'")["n"]
            if role in APPROVERS else 0),
        is_staff=role in STAFF)


@bp.get("/kb/search.json")
@login_required
def search_articles():
    """Role-scoped JSON search behind the ticket-detail attach modal (and
    reusable by any picker). Same scope as the list — an End User calling
    this directly still only ever receives Published + Public rows."""
    q = (request.args.get("q") or "").strip()

    scope_sql, params = _scope_clause()
    sql = _ARTICLE_SELECT + " WHERE 1=1" + scope_sql
    if q:
        sql += (" AND (a.title LIKE %s OR a.body LIKE %s OR EXISTS"
                "      (SELECT 1 FROM ArticleTag t"
                "        WHERE t.articleID = a.articleID AND t.tag LIKE %s))")
        params.extend([f"%{q}%"] * 3)

    rows = query_all(sql + " ORDER BY a.status = 'Published' DESC,"
                     " COALESCE(a.publishedAt, a.createdAt) DESC LIMIT 20",
                     tuple(params))
    return {"results": [
        {"articleID": a["articleID"], "title": a["title"],
         "status": a["status"], "status_label": STATUS_LABELS[a["status"]],
         "visibility": a["visibility"], "author": a["authorName"]}
        for a in rows
    ]}


# ---------------------------------------------------------------------------
# View (UC-04)
# ---------------------------------------------------------------------------

@bp.get("/kb/<int:article_id>")
@login_required
def view_article(article_id):
    a = _get_article_or_403(article_id)

    tags = query_all("SELECT tag FROM ArticleTag WHERE articleID = %s"
                     " ORDER BY tag", (article_id,))
    linked_tickets = []
    if session["role"] in STAFF:
        linked_tickets = query_all(
            "SELECT ticketID, title, status FROM Ticket"
            " WHERE linkedKBArticleID = %s ORDER BY ticketID DESC LIMIT 20",
            (article_id,))

    return render_template(
        "kb/view.html", a=_shape(a),
        tags=[t["tag"] for t in tags],
        linked_tickets=linked_tickets,
        can_edit=_can_edit(a) and a["status"] != "Archived",
        can_submit=(a["status"] == "Draft"
                    and a["authorID"] == session["user_id"]),
        can_review=(a["status"] == "PendingApproval"
                    and session["role"] in APPROVERS),
        is_staff=session["role"] in STAFF)


# ---------------------------------------------------------------------------
# Create / edit (FR-4.1) — tags synced on every save
# ---------------------------------------------------------------------------

def _read_form(form):
    return {
        "title": (form.get("title") or "").strip(),
        "body": (form.get("body") or "").strip(),
        "visibility": (form.get("visibility") or "").strip(),
        "tags": _parse_tags(form.get("tags") or ""),
    }


def _parse_tags(raw):
    """Comma-separated field → clean, deduped, ordered tag list."""
    seen, tags = set(), []
    for part in raw.split(","):
        tag = part.strip().lstrip("#")
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag[:MAX_TAG_LEN])
    return tags[:MAX_TAGS]


def _validate(f):
    errors = {}
    if not f["title"]:
        errors["title"] = "Title is required."
    elif len(f["title"]) > 150:
        errors["title"] = "Title must be 150 characters or fewer."
    if not f["body"]:
        errors["body"] = "Article body is required."
    if f["visibility"] not in VISIBILITIES:
        errors["visibility"] = "Visibility must be Internal or Public."
    return errors


def _sync_tags(article_id, new_tags):
    """One-pass tag CRUD: delete removed, insert added. Returns
    (old_csv, new_csv) if anything changed, else None — for the audit diff."""
    current = [t["tag"] for t in query_all(
        "SELECT tag FROM ArticleTag WHERE articleID = %s ORDER BY tag",
        (article_id,))]
    if sorted(new_tags, key=str.lower) == sorted(current, key=str.lower):
        return None
    for tag in current:
        if tag not in new_tags:
            execute("DELETE FROM ArticleTag"
                    " WHERE articleID = %s AND tag = %s", (article_id, tag))
    for tag in new_tags:
        if tag not in current:
            execute("INSERT INTO ArticleTag (articleID, tag)"
                    " VALUES (%s, %s)", (article_id, tag))
    return (", ".join(current) or None, ", ".join(new_tags) or None)


@bp.route("/kb/new", methods=["GET", "POST"])
@roles_required(*STAFF)
def new_article():
    if request.method == "POST":
        f = _read_form(request.form)
        errors = _validate(f)
        if errors:
            flash("Please correct the highlighted validation errors.", "error")
            return render_template("kb/form.html", mode="new", form=request.form,
                                   errors=errors,
                                   visibilities=VISIBILITIES), 400

        # "Save Draft" → Draft; "Submit for Approval" → PendingApproval.
        submit_now = request.form.get("action") == "submit"
        status = "PendingApproval" if submit_now else "Draft"

        article_id = execute(
            "INSERT INTO KBArticle (title, body, visibility, status, authorID)"
            " VALUES (%s, %s, %s, %s, %s)",
            (f["title"], f["body"], f["visibility"], status,
             session["user_id"]))

        log_action(session["user_id"], "KBArticle", article_id, "Create",
                   ip=request.remote_addr)
        tag_change = _sync_tags(article_id, f["tags"])
        if tag_change:
            log_action(session["user_id"], "KBArticle", article_id, "Update",
                       changes={"tags": tag_change}, ip=request.remote_addr)

        if submit_now:
            _notify_approvers(article_id, f["title"])
            flash(f"Article KB-{article_id} submitted for approval.", "success")
        else:
            flash(f"Draft KB-{article_id} saved.", "success")
        return redirect(url_for("kb.view_article", article_id=article_id))

    return render_template("kb/form.html", mode="new", form={}, errors={},
                           visibilities=VISIBILITIES)


@bp.route("/kb/<int:article_id>/edit", methods=["GET", "POST"])
@roles_required(*STAFF)
def edit_article(article_id):
    before = _get_article_or_403(article_id)
    if not _can_edit(before) or before["status"] == "Archived":
        abort(403)

    if request.method == "POST":
        f = _read_form(request.form)
        errors = _validate(f)
        if errors:
            flash("Please correct the highlighted validation errors.", "error")
            return render_template("kb/form.html", mode="edit", a=_shape(before),
                                   form=request.form, errors=errors,
                                   visibilities=VISIBILITIES), 400

        after = {"title": f["title"], "body": f["body"],
                 "visibility": f["visibility"]}
        changes = diff_fields(before, after, ["title", "body", "visibility"])
        tag_change = _sync_tags(article_id, f["tags"])
        if tag_change:
            changes = dict(changes or {})
            changes["tags"] = tag_change

        if not changes:
            flash("No changes to save.", "info")
            return redirect(url_for("kb.view_article", article_id=article_id))

        # FR-4.1: published content is always Manager-approved content. A
        # Technician-author editing a Published article demotes it to
        # PendingApproval for re-review; approver edits keep it Published.
        status = before["status"]
        if (before["status"] == "Published"
                and session["role"] not in APPROVERS):
            status = "PendingApproval"
            changes["status"] = ("Published", "PendingApproval")

        execute("UPDATE KBArticle SET title=%s, body=%s, visibility=%s,"
                "  status=%s WHERE articleID=%s",
                (f["title"], f["body"], f["visibility"], status, article_id))
        log_action(session["user_id"], "KBArticle", article_id, "Update",
                   changes=changes, ip=request.remote_addr)

        if status == "PendingApproval" and before["status"] == "Published":
            _notify_approvers(article_id, f["title"], reedit=True)
            flash("Changes saved — the article returns to Pending Approval "
                  "for re-review before it is published again.", "warning")
        else:
            flash("Article updated.", "success")
        return redirect(url_for("kb.view_article", article_id=article_id))

    tags = query_all("SELECT tag FROM ArticleTag WHERE articleID = %s"
                     " ORDER BY tag", (article_id,))
    return render_template("kb/form.html", mode="edit", a=_shape(before),
                           form={}, errors={}, visibilities=VISIBILITIES,
                           tags_csv=", ".join(t["tag"] for t in tags))


# ---------------------------------------------------------------------------
# Workflow transitions (FR-4.1, UC-04)
# ---------------------------------------------------------------------------

@bp.post("/kb/<int:article_id>/submit")
@roles_required(*STAFF)
def submit_article(article_id):
    a = _get_article_or_403(article_id)
    if a["authorID"] != session["user_id"]:
        abort(403)  # only the author submits their own draft
    if a["status"] != "Draft":
        flash("Only a draft can be submitted for approval.", "error")
        return redirect(url_for("kb.view_article", article_id=article_id))

    execute("UPDATE KBArticle SET status = 'PendingApproval'"
            " WHERE articleID = %s", (article_id,))
    log_action(session["user_id"], "KBArticle", article_id, "Update",
               changes={"status": ("Draft", "PendingApproval")},
               ip=request.remote_addr)
    _notify_approvers(article_id, a["title"])
    flash("Article submitted for approval.", "success")
    return redirect(url_for("kb.view_article", article_id=article_id))


@bp.post("/kb/<int:article_id>/approve")
@roles_required(*APPROVERS)
def approve_article(article_id):
    a = _get_article_or_403(article_id)
    if a["status"] != "PendingApproval":
        flash("Only an article pending approval can be published.", "error")
        return redirect(url_for("kb.view_article", article_id=article_id))

    execute("UPDATE KBArticle SET status = 'Published', approvedByID = %s,"
            "  publishedAt = NOW() WHERE articleID = %s",
            (session["user_id"], article_id))
    log_action(session["user_id"], "KBArticle", article_id, "Update",
               changes={"status": ("PendingApproval", "Published"),
                        "approvedByID": (a["approvedByID"],
                                         session["user_id"])},
               ip=request.remote_addr)
    notify(a["authorID"],
           f"KB-{article_id} approved and published",
           f"'{a['title']}' was approved by {session['name']} and is now "
           f"live ({a['visibility']} visibility).")
    flash(f"KB-{article_id} is now published.", "success")
    return redirect(url_for("kb.view_article", article_id=article_id))


@bp.post("/kb/<int:article_id>/return")
@roles_required(*APPROVERS)
def return_article(article_id):
    a = _get_article_or_403(article_id)
    if a["status"] != "PendingApproval":
        flash("Only an article pending approval can be returned.", "error")
        return redirect(url_for("kb.view_article", article_id=article_id))

    reason = (request.form.get("reason") or "").strip()
    execute("UPDATE KBArticle SET status = 'Draft' WHERE articleID = %s",
            (article_id,))
    log_action(session["user_id"], "KBArticle", article_id, "Update",
               changes={"status": ("PendingApproval", "Draft")},
               ip=request.remote_addr)
    notify(a["authorID"],
           f"KB-{article_id} returned to draft",
           f"'{a['title']}' was returned by {session['name']}."
           + (f" Reviewer note: {reason}" if reason else ""))
    flash("Article returned to the author as a draft.", "success")
    return redirect(url_for("kb.view_article", article_id=article_id))


# ---------------------------------------------------------------------------
# Ticket integration (FR-4.2) — resolution article on Ticket
# ---------------------------------------------------------------------------

@bp.post("/tickets/<int:ticket_id>/kb-article")
@roles_required(*STAFF)
def link_ticket_article(ticket_id):
    """Set or clear Ticket.linkedKBArticleID (the resolution article
    designated at close). Empty article_id clears the link. Only a
    Published article may be designated — a draft is not a resolution."""
    t = _get_ticket_or_403(ticket_id)
    raw = (request.form.get("article_id") or "").strip()

    if not raw:  # clear
        if t["linkedKBArticleID"] is not None:
            execute("UPDATE Ticket SET linkedKBArticleID = NULL"
                    " WHERE ticketID = %s", (ticket_id,))
            log_action(session["user_id"], "Ticket", ticket_id, "Update",
                       changes={"linkedKBArticleID":
                                (t["linkedKBArticleID"], None)},
                       ip=request.remote_addr)
            flash("Resolution article removed.", "success")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    if not raw.isdigit():
        abort(400)
    article = query_one("SELECT articleID, title, status FROM KBArticle"
                        " WHERE articleID = %s", (int(raw),))
    if article is None or article["status"] != "Published":
        flash("Only a published KB article can be set as the resolution "
              "article.", "error")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))

    if t["linkedKBArticleID"] != article["articleID"]:
        execute("UPDATE Ticket SET linkedKBArticleID = %s WHERE ticketID = %s",
                (article["articleID"], ticket_id))
        log_action(session["user_id"], "Ticket", ticket_id, "Update",
                   changes={"linkedKBArticleID":
                            (t["linkedKBArticleID"], article["articleID"])},
                   ip=request.remote_addr)
    flash(f"KB-{article['articleID']} set as the resolution article.",
          "success")
    return redirect(url_for("tickets.view_ticket", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _notify_approvers(article_id, title, reedit=False):
    what = ("was edited and needs re-approval" if reedit
            else "is awaiting your approval")
    for m in query_all("SELECT userID FROM User WHERE role IN"
                       " ('Manager', 'Administrator') AND status = 'Active'"):
        notify(m["userID"], f"KB-{article_id} pending approval",
               f"'{title}' {what}.")


def _shape(a):
    a = dict(a)
    a["status_label"] = STATUS_LABELS[a["status"]]
    a["status_badge"] = STATUS_BADGES[a["status"]]
    a["visibility_badge"] = VISIBILITY_BADGES[a["visibility"]]
    a["created_label"] = (a["createdAt"].strftime("%b %d, %Y")
                          if a.get("createdAt") else "—")
    a["published_label"] = (a["publishedAt"].strftime("%b %d, %Y")
                            if a.get("publishedAt") else None)
    return a

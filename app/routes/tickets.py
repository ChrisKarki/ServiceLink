from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)

from ..db import execute, log_audit, query_all
from .auth import login_required

bp = Blueprint("tickets", __name__)


@bp.get("/health")
def health():
    # Deliberately touches no DB, so it proves app wiring independent of MySQL.
    return {"status": "ok"}


@bp.route("/tickets/new", methods=["GET", "POST"])
@login_required
def create_ticket():
    categories = query_all(
        "SELECT categoryID, name FROM Category WHERE isActive = TRUE ORDER BY name"
    )
    priorities = ["Low", "Medium", "High", "Critical"]

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category_id = request.form.get("category_id", "").strip()
        priority = request.form.get("priority", "").strip()

        errors = {}

        # Server-side validation per NFR-S4
        if not title:
            errors["title"] = "Summary / Title is required."
        elif len(title) > 150:
            errors["title"] = "Title must be 150 characters or fewer."

        if not description:
            errors["description"] = "Detailed Description is required."

        if not category_id:
            errors["category_id"] = "Please select a Category."
        else:
            try:
                cat_id_int = int(category_id)
                valid_cat = any(
                    str(c["categoryID"]) == str(cat_id_int) for c in categories
                )
                if not valid_cat:
                    errors["category_id"] = "Selected category is not valid."
            except ValueError:
                errors["category_id"] = "Invalid category format."

        if not priority:
            errors["priority"] = "Please select a Priority level."
        elif priority not in priorities:
            errors["priority"] = "Invalid priority selection."

        # EX-1: Validation errors preserve form data
        if errors:
            flash("Please correct the highlighted validation errors.", "error")
            return (
                render_template(
                    "tickets/new.html",
                    categories=categories,
                    priorities=priorities,
                    form=request.form,
                    errors=errors,
                    str=str,
                ),
                400,
            )

        # UC-01 Main flow: Create ticket with status 'New' and audit-logged
        ticket_id = execute(
            """
            INSERT INTO Ticket (
                title, description, categoryID, priority, status,
                submittedByUserID, createdAt
            )
            VALUES (%s, %s, %s, %s, 'New', %s, NOW())
            """,
            (
                title,
                description,
                int(category_id),
                priority,
                session["user_id"],
            ),
        )

        log_audit(
            actor_id=session["user_id"],
            entity_type="Ticket",
            entity_id=ticket_id,
            action="Create",
            ip_address=request.remote_addr,
        )

        flash(f"Ticket #{ticket_id} created successfully!", "success")
        return redirect(url_for("main.dashboard"))

    return render_template(
        "tickets/new.html",
        categories=categories,
        priorities=priorities,
        form={},
        errors={},
        str=str,
    )

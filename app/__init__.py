"""ServiceLink application factory.

Replaces the skeleton __init__.py: adds SECRET_KEY (sessions cannot work
without it), session hardening, and the auth/main blueprints alongside
the existing tickets/admin stubs.
"""

import os
from datetime import datetime
from werkzeug.exceptions import HTTPException
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for


def create_app():
    load_dotenv()
    app = Flask(__name__)

    app.config["DEBUG"] = os.environ.get("FLASK_DEBUG") == "1"
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]  # fail loudly if missing

    # FR-1.1 session policy. The lifetime below is a backstop; the ABSOLUTE
    # 8-hour timeout is enforced in auth.login_required, because Flask's
    # cookie lifetime is a sliding window by default.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    app.config["SESSION_REFRESH_EACH_REQUEST"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Set to True once the VM serves HTTPS (NFR-S3):
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE") == "1"

    # Werkzeug drops the request at the socket once the body exceeds this, so
    # a hostile multi-gigabyte upload is never buffered. The margin above the
    # per-file ceiling covers multipart overhead and batched uploads; the real
    # 25 MB limit is enforced per file in services/attachments.py.
    from .services import attachments
    app.config["MAX_CONTENT_LENGTH"] = attachments.MAX_BYTES * 4

    from .routes.admin import bp as admin_bp
    from .routes.auth import bp as auth_bp
    from .routes.kb import bp as kb_bp
    from .routes.main import bp as main_bp
    from .routes.reports import bp as reports_bp
    from .routes.resources import bp as resources_bp
    from .routes.search import bp as search_bp
    from .routes.team import bp as team_bp
    from .routes.tickets import bp as tickets_bp
    from .routes.notifications import bp as notifications_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(resources_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(kb_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(team_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(notifications_bp)


    @app.errorhandler(404)
    def not_found(_e):
        return render_template("404.html"), 404
    
    @app.errorhandler(403)
    def forbidden(_e):
        # Friendly page for authenticated users hitting a route their role
        # does not permit (FR-1.2). Anonymous users never reach here —
        # login_required redirects them to /login first.
        return render_template("403.html"), 403

    @app.errorhandler(413)
    def too_large(_e):
        # MAX_CONTENT_LENGTH fires before any view runs, so the ticket routes
        # never see the request and cannot flash anything themselves. This
        # turns the raw werkzeug error into the message TC-01 AF-2 expects.
        flash(f"That upload is too large. Each file must be "
              f"{attachments.MAX_MB_LABEL} or smaller.", "error")
        return redirect(request.referrer or url_for("main.dashboard")), 302

    @app.errorhandler(500)
    def server_error(e):
        # Standalone template on purpose: base.html queries the Notification
        # table, so rendering it here would raise again if the database is
        # what failed. The reference is the timestamp the traceback was
        # logged under, so a user can quote it and it can be found in
        # journalctl without them pasting a stack trace.
        reference = datetime.now().strftime("SL-%Y%m%d-%H%M%S")
        app.logger.exception("Unhandled server error [%s]", reference)
        return render_template("500.html", reference=reference), 500



    @app.errorhandler(Exception)
    def unhandled(e):
        # Werkzeug HTTP errors (403/404/413) keep their own handlers; only
        # genuine unhandled exceptions fall through to the 500 page. Without
        # this, an exception raised outside a view — in a context processor,
        # for instance — bypasses the 500 handler entirely.
        if isinstance(e, HTTPException):
            return e
        return server_error(e)

    return app
"""ServiceLink application factory.

Adds SECRET_KEY (sessions cannot work without it), session hardening,
blueprint registration for all live modules, and the 403/404 error
handlers that back roles_required's abort(403) and the abort(404)
calls in tickets/resources.
"""

import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, render_template


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

    from .routes.admin import bp as admin_bp
    from .routes.auth import bp as auth_bp
    from .routes.main import bp as main_bp
    from .routes.resources import bp as resources_bp
    from .routes.tickets import bp as tickets_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(resources_bp)
    app.register_blueprint(admin_bp)

    # roles_required aborts with 403 (FR-1.2 denial semantics); render the
    # branded page instead of Flask's default. 404 covers abort(404) on
    # unknown ticket/resource IDs.
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("404.html"), 404

    return app

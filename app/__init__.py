"""ServiceLink application factory.

Replaces the skeleton __init__.py: adds SECRET_KEY (sessions cannot work
without it), session hardening, and the auth/main blueprints alongside
the existing tickets/admin stubs.
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

    from .routes.auth import bp as auth_bp
    from .routes.main import bp as main_bp
    from .routes.resources import bp as resources_bp
    from .routes.tickets import bp as tickets_bp
    from .routes.admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(resources_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(admin_bp)

    @app.errorhandler(403)
    def forbidden(_):
        # Friendly page for authenticated users hitting a route their role
        # does not permit (FR-1.2). Anonymous users never reach here —
        # login_required redirects them to /login first.
        return render_template("403.html"), 403

    return app

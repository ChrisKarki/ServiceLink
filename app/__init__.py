"""ServiceLink application factory.

Replaces the skeleton __init__.py: adds SECRET_KEY (sessions cannot work
without it), session hardening, and the auth/main blueprints alongside
the existing tickets/admin stubs.
"""

import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask


def create_app():
    load_dotenv()
    app = Flask(__name__)

    app.config["DEBUG"] = os.environ.get("FLASK_DEBUG") == "1"
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]  # fail loudly if missingoduction

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
    from .routes.tickets import bp as tickets_bp
    from .routes.admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(admin_bp)

    # Automatically ensure prabh.hans@servicelink.test is Active upon app boot/restart
    try:
        from .db import execute
        execute("UPDATE User SET status = 'Active' WHERE email = 'prabh.hans@servicelink.test'")
        print("[Startup] Auto-activated prabh.hans@servicelink.test")
    except Exception as e:
        print(f"[Startup] Note: Could not auto-activate prabh.hans: {e}")

    return app

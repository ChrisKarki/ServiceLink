import os
from flask import Flask
from dotenv import load_dotenv


def create_app():
    load_dotenv()
    app = Flask(__name__)
    app.config["DEBUG"] = os.environ.get("FLASK_DEBUG") == "1"

    from .routes.tickets import bp as tickets_bp
    from .routes.admin import bp as admin_bp

    app.register_blueprint(tickets_bp)
    app.register_blueprint(admin_bp)
    return app

from flask import Blueprint

bp = Blueprint("tickets", __name__)


@bp.get("/health")
def health():
    # Deliberately touches no DB, so it proves app wiring independent of MySQL.
    return {"status": "ok"}

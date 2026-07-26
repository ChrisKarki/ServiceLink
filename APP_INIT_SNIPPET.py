"""Add these two blocks to app/__init__.py inside create_app().

Both are required. Without them a 26 MB upload produces either a raw
werkzeug 413 page or a broken pipe, instead of the clear message TC-01 AF-2
asks for.
"""

# ---------------------------------------------------------------------------
# 1. Request size ceiling — near the other app.config lines
# ---------------------------------------------------------------------------
# Werkzeug aborts the request once the body exceeds this, so a hostile 2 GB
# upload is dropped at the socket rather than buffered. The margin above
# MAX_BYTES covers multipart overhead and the extra files in a batch: the
# per-file 25 MB limit is enforced properly in services/attachments.py.

from .services import attachments  # noqa: E402

app.config["MAX_CONTENT_LENGTH"] = attachments.MAX_BYTES * 4


# ---------------------------------------------------------------------------
# 2. Friendly 413 — after the blueprints are registered
# ---------------------------------------------------------------------------
# MAX_CONTENT_LENGTH fires before any view runs, so the ticket routes never
# see the request and cannot flash anything. This handler turns the raw
# werkzeug error into the message TC-01 AF-2 expects, and returns the user
# to where they were.

from flask import flash, redirect, request, url_for  # noqa: E402


@app.errorhandler(413)
def _too_large(_e):
    flash(f"That upload is too large. Each file must be "
          f"{attachments.MAX_MB_LABEL} or smaller.", "error")
    return redirect(request.referrer or url_for("main.dashboard")), 302


# ---------------------------------------------------------------------------
# 3. Upload directory (optional, but do this on the VM)
# ---------------------------------------------------------------------------
# attachments.upload_dir() defaults to <instance_path>/uploads, which is
# outside static/ and therefore not web-served. Under gunicorn the working
# directory may not be the project root, so set an absolute path:
#
#     UPLOAD_DIR=/home/opc/servicelink/uploads
#
# in .env, and make sure the service user can write to it:
#
#     mkdir -p /home/opc/servicelink/uploads
#     chmod 750 /home/opc/servicelink/uploads
#
# Add 'uploads/' and 'instance/' to .gitignore — user-uploaded files should
# never enter the repository.

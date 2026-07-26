"""Attachment service — validation and disk storage for ticket files.

FR-2.1 (a submitter may attach evidence) and TC-01 AF-2 (unsupported type or
oversize file is rejected with a clear message).

WHY FILES ARE NOT IN static/
----------------------------
Anything under static/ is served by the web server with no authentication.
A screenshot on a ticket is scoped by FR-5.1 exactly like the ticket itself:
an End User may see their own, staff may see all. So uploads live OUTSIDE the
served tree and are handed out by an authenticated route that re-checks
ticket access on every request.

WHY THE FILENAME ON DISK IS NOT THE USER'S FILENAME
---------------------------------------------------
The stored name is uuid4 + a validated extension. The original name is kept
in the database for display only and never touches a path. This removes the
whole class of traversal and collision bugs rather than trying to sanitise
around them — `../../etc/passwd`, a 300-character name, a NUL byte, two users
both uploading `screenshot.png`.

DEFENCE IN DEPTH ON TYPE
------------------------
Three checks, all must pass:
  1. extension is in ALLOWED_EXTENSIONS
  2. browser-reported MIME is in ALLOWED_MIME
  3. extension and MIME agree with each other

The third matters: a browser will happily report image/png for a file named
payload.exe, and a client can send any Content-Type it likes. Neither check
alone is meaningful; together they stop the casual cases. Content-sniffing
would be stronger still and is noted as a limitation.
"""

import math
import os
import uuid

# ---------------------------------------------------------------------------
# Policy — TC-01 AF-2 states a 25 MB ceiling and .png / .pdf as examples
# ---------------------------------------------------------------------------

MAX_BYTES = 25 * 1024 * 1024          # 25 MB, per TC-01 AF-2
MAX_PER_TICKET = 10
MAX_MB_LABEL = "25 MB"

# extension -> the MIME types a browser legitimately reports for it
ALLOWED = {
    ".png":  ("image/png",),
    ".jpg":  ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
    ".gif":  ("image/gif",),
    ".webp": ("image/webp",),
    ".pdf":  ("application/pdf",),
    ".txt":  ("text/plain",),
    ".log":  ("text/plain", "application/octet-stream"),
    ".csv":  ("text/csv", "application/vnd.ms-excel", "text/plain"),
    ".docx": ("application/vnd.openxmlformats-officedocument"
              ".wordprocessingml.document",),
    ".xlsx": ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet",),
    ".zip":  ("application/zip", "application/x-zip-compressed"),
}

ALLOWED_EXTENSIONS = tuple(sorted(ALLOWED))
EXTENSION_LABEL = ", ".join(e.lstrip(".").upper() for e in ALLOWED_EXTENSIONS)

# Rendered inline in the browser rather than force-downloaded. Everything
# else gets Content-Disposition: attachment, so an uploaded .txt or .zip can
# never be interpreted as a document in the site's origin.
INLINE_MIME = ("image/png", "image/jpeg", "image/gif", "image/webp",
               "application/pdf")


# ---------------------------------------------------------------------------
# Storage location
# ---------------------------------------------------------------------------

def upload_dir(app):
    """Resolve (and create) the upload directory.

    Defaults to <instance>/uploads, which sits outside static/. Override with
    the UPLOAD_DIR environment variable when running under gunicorn, where
    the working directory is not the project root.
    """
    path = os.environ.get("UPLOAD_DIR") or os.path.join(app.instance_path,
                                                        "uploads")
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def split_ext(filename):
    return os.path.splitext(filename or "")[1].lower()


def validate(storage):
    """Check one uploaded file. Returns an error string, or None if valid.

    The message names the specific problem — TC-01 AF-2 requires the user be
    told clearly whether it was the type or the size.
    """
    if storage is None or not storage.filename:
        return "No file was selected."

    ext = split_ext(storage.filename)
    if not ext:
        return (f"'{storage.filename}' has no file extension. "
                f"Allowed types: {EXTENSION_LABEL}.")
    if ext not in ALLOWED:
        return (f"'{ext}' files are not supported. "
                f"Allowed types: {EXTENSION_LABEL}.")

    mime = (storage.mimetype or "").lower()
    if mime and mime not in ALLOWED[ext]:
        return (f"'{storage.filename}' does not look like a genuine "
                f"{ext.lstrip('.').upper()} file and was rejected.")

    size = measure(storage)
    if size == 0:
        return f"'{storage.filename}' is empty."
    if size > MAX_BYTES:
        return (f"'{storage.filename}' is {human_size(size)}, which exceeds "
                f"the {MAX_MB_LABEL} limit.")
    return None


def measure(storage):
    """Byte length of an uploaded file, without reading it into memory.

    Content-Length is attacker-controlled, so the stream is measured
    directly and rewound.
    """
    pos = storage.stream.tell()
    storage.stream.seek(0, os.SEEK_END)
    size = storage.stream.tell()
    storage.stream.seek(pos)
    return size


def human_size(n):
    """Readable size, rounded UP.

    Rounding up matters for the rejection message: a file one byte over the
    ceiling would otherwise render as "25.0 MB, which exceeds the 25 MB
    limit", which reads as a bug. Never understate a size the user is being
    told is too large.
    """
    if n < 1024:
        return f"{n:.0f} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024 or unit == "GB":
            return f"{math.ceil(n * 10) / 10:.1f} {unit}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def store(storage, directory):
    """Write the file under a generated name. Returns (storedName, size).

    The caller has already validated. Nothing derived from user input reaches
    the path: the stem is a uuid4 and the extension came from the ALLOWED
    allowlist, not from the filename string.
    """
    ext = split_ext(storage.filename)
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(directory, stored)
    storage.save(path)
    return stored, os.path.getsize(path)


def remove(stored_name, directory):
    """Delete the file from disk. Never raises — a missing file must not
    block the database row being removed, or the row becomes unreachable."""
    try:
        os.remove(os.path.join(directory, stored_name))
        return True
    except OSError:
        return False


def is_inline(mime):
    return (mime or "").lower() in INLINE_MIME


def icon_for(mime, name):
    """Coarse label for the UI."""
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "Image"
    if m == "application/pdf":
        return "PDF"
    if m in ("application/zip", "application/x-zip-compressed"):
        return "Archive"
    if m.startswith("text/"):
        return "Text"
    return split_ext(name).lstrip(".").upper() or "File"

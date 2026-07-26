"""Notification service (FR-2.5) — one contract, pluggable delivery.

By default the prototype does NOT send real email: notifications are written
to logs/notifications.log (and the Flask console) in an email-shaped format so
the demo can show WHAT would have been sent and to WHOM. Real SMTP is opt-in
(see _deliver) and off unless configured — the stub satisfies the demo.

Every notification fans out to three channels:
    1. logs/notifications.log  — always, the demo and audit record
    2. Notification table      — always, backs the topbar bell (in-app)
    3. SMTP                    — only when NOTIFY_SMTP_HOST is configured

THE CONTRACT (do not change without a team decision):

    send(user_id, subject, body, link=None) -> bool

`link` is additive and optional, so all existing call sites are unchanged.
It is the relative path the bell entry opens, e.g. "/tickets/42". When
omitted, _infer_link() derives one from the subject where it safely can.

Callers code against send() only. Changing the relay touches ONLY
_deliver(); adding a channel touches ONLY send().

FR-2.5 REGISTRY — every event that must call send(), its recipients, and the
single call site that fires it. P3.1 verifies this list against the code:

    Event                     Recipient(s)                Call site (module)
    ------------------------  --------------------------  ------------------------
    Ticket auto-assignment    assigned technician         _auto_assign        (P1.2)
    No technician available   active managers             _auto_assign AF-1   (P1.2)
    Ticket status change      submitter                   change_status       (P1.4)
    Submitter reopen          assigned technician         reopen_ticket       (P2.2)
    New public comment        submitter (when not author) add_comment         (P2.1)
    SLA breach                assigned tech + managers     _check_and_escalate_breach (P2.2)
    Escalation                active managers             escalate_ticket     (P2.3)

Known limitation (tracked): a Public comment authored BY the submitter does
not yet notify the assigned technician (the reverse "other party" direction).
add_comment notifies the submitter and skips self-authored notifications;
wiring the technician side is a follow-up in the comments feature.

Enabling real SMTP (optional, per RAD §3.1.3 the relay is customer-provided):
set NOTIFY_SMTP_HOST (+ NOTIFY_SMTP_PORT / NOTIFY_SMTP_USER /
NOTIFY_SMTP_PASSWORD / NOTIFY_FROM / NOTIFY_SMTP_TLS). Unset = log-only stub.
"""

import logging
import os
import re

from ..db import execute, query_one

_logger = None


def _get_logger():
    """Lazy logger so importing this module never touches the filesystem."""
    global _logger
    if _logger is None:
        os.makedirs("logs", exist_ok=True)
        _logger = logging.getLogger("servicelink.notify")
        _logger.setLevel(logging.INFO)
        if not _logger.handlers:
            fmt = logging.Formatter("%(asctime)s  %(message)s")
            file_handler = logging.FileHandler("logs/notifications.log")
            file_handler.setFormatter(fmt)
            console = logging.StreamHandler()
            console.setFormatter(fmt)
            _logger.addHandler(file_handler)
            _logger.addHandler(console)
        _logger.propagate = False
    return _logger


def send(user_id, subject, body, link=None):
    """Notify a user. Returns True if 'delivered', False if user unknown.

    Never raises on a missing user — a notification failure must not
    roll back the business action that triggered it.
    """
    user = query_one(
        "SELECT email, firstName FROM User WHERE userID = %s", (user_id,)
    )
    if user is None:
        _get_logger().warning(
            "DROPPED | unknown userID=%s | subject=%r", user_id, subject
        )
        return False

    _store(user_id, subject, body,
           link if link is not None else _infer_link(subject))
    _deliver(user["email"], subject, body)
    return True


def _store(user_id, subject, body, link):
    """Write the in-app copy that backs the topbar bell.

    Swallows its own failures for the same reason _deliver does: if the
    Notification table is missing or the insert fails, the ticket update
    that triggered this must still stand. The log line remains the record.
    """
    try:
        execute("INSERT INTO Notification (userID, subject, body, link)"
                " VALUES (%s, %s, %s, %s)",
                (user_id, subject[:255], body, link or None))
    except Exception as exc:  # noqa: BLE001 — see the send() contract
        _get_logger().warning(
            "in-app notification not stored for userID=%s: %s", user_id, exc)


# Subjects across the codebase consistently name their entity as "#42" or
# "KB-7", so the bell is useful immediately without editing all thirteen
# existing call sites. An explicit link= always wins; this is the fallback.
_ARTICLE_RE = re.compile(r"\bKB-(\d+)")
_TICKET_RE = re.compile(r"#(\d+)")


def _infer_link(subject):
    m = _ARTICLE_RE.search(subject or "")
    if m:
        return f"/kb/{m.group(1)}"
    m = _TICKET_RE.search(subject or "")
    if m:
        return f"/tickets/{m.group(1)}"
    return None


def _deliver(email, subject, body):
    """Delivery backend.

    Always logs the email-shaped line (so the demo log is the record of what
    was sent). If NOTIFY_SMTP_HOST is configured, ALSO attempts real SMTP
    delivery; any SMTP failure is caught and logged, never raised — a delivery
    problem must not break the business action that triggered it (same
    guarantee as send()).
    """
    _get_logger().info("TO: %s | SUBJECT: %s | BODY: %s", email, subject, body)

    if not os.environ.get("NOTIFY_SMTP_HOST"):
        return  # log-only stub — the demo default

    try:
        _smtp_send(email, subject, body)
        _get_logger().info("SMTP delivered to %s", email)
    except Exception as exc:  # noqa: BLE001 — never propagate (see send() contract)
        _get_logger().warning("SMTP delivery to %s failed: %s", email, exc)


def _smtp_send(email, subject, body):
    """Send one message through the configured SMTP relay. Isolated so the
    stub path never imports smtplib and the swap-in stays contained here."""
    import smtplib
    from email.message import EmailMessage

    host = os.environ["NOTIFY_SMTP_HOST"]
    port = int(os.environ.get("NOTIFY_SMTP_PORT", 587))
    user = os.environ.get("NOTIFY_SMTP_USER")
    password = os.environ.get("NOTIFY_SMTP_PASSWORD")
    sender = os.environ.get("NOTIFY_FROM") or user or "no-reply@servicelink.local"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        if os.environ.get("NOTIFY_SMTP_TLS", "1") != "0":
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)

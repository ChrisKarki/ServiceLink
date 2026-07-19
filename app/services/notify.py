"""Notification service (FR-2.5) — one contract, pluggable delivery.

By default the prototype does NOT send real email: notifications are written
to logs/notifications.log (and the Flask console) in an email-shaped format so
the demo can show WHAT would have been sent and to WHOM. Real SMTP is opt-in
(see _deliver) and off unless configured — the stub satisfies the demo.

THE CONTRACT (do not change without a team decision):

    send(user_id, subject, body) -> bool

Callers code against send() only. Wiring a real SMTP relay changes ONLY
_deliver() below — zero call sites move.

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

from ..db import query_one

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


def send(user_id, subject, body):
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

    _deliver(user["email"], subject, body)
    return True


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

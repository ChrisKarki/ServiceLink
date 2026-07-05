"""Notification service (FR-2.5) — stub delivery, frozen interface.

The prototype does not send real email. Notifications are written to
logs/notifications.log (and the Flask console) in an email-shaped format
so the demo can show WHAT would have been sent and to WHOM.

THE CONTRACT (do not change without a team decision):

    send(user_id, subject, body) -> bool

Prabh and Hiten code against send() now. When/if we wire a real SMTP
relay (per RAD §3.1.3 the relay is customer-provided and out of the
core flow), ONLY _deliver() below changes — zero call sites move.

Events that MUST call send() (FR-2.5 checklist, P3.1 verifies this):
  - Ticket status change            -> submitter
  - New public comment              -> submitter (or technician, whoever
                                       did not author it)
  - Ticket assignment / reassignment-> assigned technician
  - SLA breach                      -> assigned technician AND manager
  - Escalation                      -> manager
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
    """Delivery backend. Stub: log in an email-shaped format.

    SMTP swap-in later replaces the body of this function only.
    """
    _get_logger().info("TO: %s | SUBJECT: %s | BODY: %s", email, subject, body)

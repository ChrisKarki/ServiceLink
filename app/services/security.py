"""Security service — system settings, TOTP enrolment, and reset OTPs.

Split out of the auth blueprint so the routes stay readable and so the
crypto decisions live in exactly one place. Nothing here touches the
session or the request; everything is a pure function of its arguments
plus the database.

FR-1.1  TOTP multi-factor authentication
FR-1.2  Password reset via emailed one-time code

Design notes worth keeping:

  * The TOTP secret is only written to User.totpSecret AFTER the user has
    proved they scanned it (they type a valid code). A secret persisted at
    QR-render time would lock out anyone who closed the tab mid-enrolment.

  * Reset codes are stored bcrypt-hashed. A database read must not yield a
    working reset code, for the same reason passwordHash exists.

  * A code is single-use, time-boxed, and attempt-capped. Six digits is
    only safe because of those three together.

  * Clearing totpSecret IS "reset MFA" — a password reset and an
    Administrator-initiated reset are the same operation, so there is one
    code path, not two.
"""

import base64
import io
import secrets

import bcrypt
import pyotp
import qrcode

from ..db import execute, query_one

# ---------------------------------------------------------------------------
# Policy constants — the single place these numbers are defined
# ---------------------------------------------------------------------------

TOTP_ISSUER = "ServiceLink"
TOTP_VALID_WINDOW = 1        # +/- one 30s step, tolerates modest clock drift

OTP_LENGTH = 6
OTP_TTL_MINUTES = 15
OTP_MAX_ATTEMPTS = 5

MIN_PASSWORD_LEN = 10
BCRYPT_ROUNDS = 12


# ---------------------------------------------------------------------------
# System settings (SystemSetting table)
# ---------------------------------------------------------------------------

def get_setting(key, default=None):
    row = query_one("SELECT settingValue FROM SystemSetting"
                    " WHERE settingKey = %s", (key,))
    return row["settingValue"] if row else default


def set_setting(key, value, actor_id=None):
    execute("INSERT INTO SystemSetting (settingKey, settingValue, updatedByID)"
            " VALUES (%s, %s, %s)"
            " ON DUPLICATE KEY UPDATE settingValue = VALUES(settingValue),"
            "                         updatedByID  = VALUES(updatedByID)",
            (key, str(value), actor_id))


def mfa_required():
    """Is TOTP enrolment mandatory for every account?

    Defaults to True when the row is missing, so a failed migration fails
    closed rather than silently disabling MFA for everyone.
    """
    return get_setting("mfa_required", "1") == "1"


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

def hash_password(plain):
    return bcrypt.hashpw(plain.encode(),
                         bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def check_password(plain, stored_hash):
    return bcrypt.checkpw(plain.encode(), stored_hash.encode()
                          if isinstance(stored_hash, str) else stored_hash)


def validate_password(password, confirm):
    """NFR-S1. Length is the dominant factor, so the floor is 10 rather
    than the more common 8, plus a mixed-content rule. Returns a dict of
    field -> message, empty when acceptable."""
    errors = {}
    if len(password) < MIN_PASSWORD_LEN:
        errors["password"] = (f"Password must be at least "
                              f"{MIN_PASSWORD_LEN} characters.")
    elif password.isdigit() or password.isalpha():
        errors["password"] = ("Password must combine letters with at least "
                              "one number or symbol.")
    if password != confirm:
        errors["confirm"] = "Passwords do not match."
    return errors


# ---------------------------------------------------------------------------
# TOTP (FR-1.1)
# ---------------------------------------------------------------------------

def new_totp_secret():
    return pyotp.random_base32()


def provisioning_uri(secret, email):
    """otpauth:// URI consumed by Google Authenticator, Authy, 1Password,
    Microsoft Authenticator, etc."""
    return pyotp.TOTP(secret).provisioning_uri(name=email,
                                               issuer_name=TOTP_ISSUER)


def qr_data_uri(secret, email):
    """Return the enrolment QR as an inline data URI.

    Inline rather than a served file: the provisioning URI contains the
    shared secret, so writing it to static/ would leave the secret readable
    by anyone who can guess a filename.
    """
    img = qrcode.make(provisioning_uri(secret, email))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def verify_totp(secret, code):
    """Verify a 6-digit code. Tolerates spaces, which phone keyboards and
    password managers both like to insert."""
    if not secret or not code:
        return False
    cleaned = "".join(ch for ch in str(code) if ch.isdigit())
    if len(cleaned) != 6:
        return False
    try:
        return pyotp.TOTP(secret).verify(cleaned,
                                         valid_window=TOTP_VALID_WINDOW)
    except Exception:
        return False


def enrol_totp(user_id, secret):
    """Persist a secret the user has already proved they can generate
    codes from."""
    execute("UPDATE User SET totpSecret = %s, totpEnrolledAt = NOW(),"
            "                  mfaEnabled = 1"
            " WHERE userID = %s", (secret, user_id))


def reset_totp(user_id):
    """Clear MFA enrolment. The next sign-in re-enrols if MFA is required.
    Used by both the password-reset flow and the Administrator action."""
    execute("UPDATE User SET totpSecret = NULL, totpEnrolledAt = NULL,"
            "                  mfaEnabled = 0"
            " WHERE userID = %s", (user_id,))


# ---------------------------------------------------------------------------
# Password-reset one-time codes (FR-1.2)
# ---------------------------------------------------------------------------

def issue_reset_otp(user_id, ip=None):
    """Invalidate any outstanding codes, then mint and store a new one.

    Returns the PLAINTEXT code for emailing. It is never stored, logged, or
    returned to the browser — the only copy that leaves this function goes
    into the notification body.
    """
    execute("UPDATE PasswordResetOTP SET usedAt = NOW()"
            "  WHERE userID = %s AND usedAt IS NULL", (user_id,))

    code = f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"
    execute("INSERT INTO PasswordResetOTP"
            "   (userID, codeHash, expiresAt, requestIP)"
            " VALUES (%s, %s, NOW() + INTERVAL %s MINUTE, %s)",
            (user_id, hash_password(code), OTP_TTL_MINUTES, ip))
    return code


def verify_reset_otp(user_id, code):
    """Check a submitted code against the newest outstanding row.

    Returns (ok, reason). `reason` is a short machine key the caller turns
    into a message; it is deliberately NOT surfaced verbatim, so a wrong
    code and an expired code look identical to an attacker.
    """
    row = query_one(
        "SELECT otpID, codeHash, attempts,"
        "       (expiresAt < NOW()) AS expired"
        "  FROM PasswordResetOTP"
        " WHERE userID = %s AND usedAt IS NULL"
        " ORDER BY otpID DESC LIMIT 1", (user_id,))

    if row is None:
        return False, "none"
    if row["expired"]:
        return False, "expired"
    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        return False, "locked"

    cleaned = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not cleaned or not check_password(cleaned, row["codeHash"]):
        execute("UPDATE PasswordResetOTP SET attempts = attempts + 1"
                " WHERE otpID = %s", (row["otpID"],))
        return False, "mismatch"

    execute("UPDATE PasswordResetOTP SET usedAt = NOW() WHERE otpID = %s",
            (row["otpID"],))
    return True, "ok"

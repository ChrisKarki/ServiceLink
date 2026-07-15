"""Database access layer.

Keeps the lazy connection pool from the skeleton (so /health never touches
the DB) and adds three helpers. Every query goes through these with
parameterized placeholders only — string-formatted SQL is banned (NFR-S4).
"""

import os
from mysql.connector import pooling

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="servicelink_pool",
            pool_size=5,
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", 3306)),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            database=os.environ["DB_NAME"],
        )
    return _pool


def query_one(sql, params=()):
    """Run a SELECT and return the first row as a dict, or None."""
    conn = _get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        conn.close()


def query_all(sql, params=()):
    """Run a SELECT and return all rows as a list of dicts."""
    conn = _get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE inside a transaction.

    Returns the last inserted row id (for INSERTs with AUTO_INCREMENT)
    or the affected row count otherwise.
    """
    conn = _get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        result = cur.lastrowid if cur.lastrowid else cur.rowcount
        cur.close()
        return result
    finally:
        conn.close()

def get_connection():
    """Raw pooled connection — for multi-statement transactions only
    (the audit service writes AuditLog + AuditLogChange atomically).
    Caller owns commit/rollback/close. Everything else should keep
    using query_one / query_all / execute."""
    return _get_pool().get_connection()

def log_audit(actor_id, entity_type, entity_id, action, ip_address, changes=None):
    """Insert a record into AuditLog and optional field diffs into AuditLogChange."""
    log_id = execute(
        """
        INSERT INTO AuditLog (actorID, entityType, entityID, action, ipAddress, timestamp)
        VALUES (%s, %s, %s, %s, %s, NOW())
        """,
        (actor_id, entity_type, entity_id, action, ip_address or "127.0.0.1"),
    )
    if changes:
        conn = _get_pool().get_connection()
        try:
            cur = conn.cursor()
            for field_name, old_val, new_val in changes:
                cur.execute(
                    """
                    INSERT INTO AuditLogChange (logID, fieldName, oldValue, newValue)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        log_id,
                        field_name,
                        str(old_val) if old_val is not None else None,
                        str(new_val) if new_val is not None else None,
                    ),
                )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    return log_id


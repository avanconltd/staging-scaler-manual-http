"""Per-user login (HTTP Basic Auth checked against dbo.ScalerUsers) and the
dbo.ScalerAuditLog trail of every call. Schema: sql/schema.sql."""
import base64
import binascii
import hashlib
import hmac
import os
import secrets

import pyodbc

PBKDF2_ITERATIONS = 200_000

# Compared against when the username doesn't exist, so a wrong username takes
# as long as a wrong password and doesn't reveal which usernames are valid.
_DUMMY_SALT = "00" * 16
_DUMMY_HASH = "00" * 32


def _odbc_escape(value):
    return "{" + value.replace("}", "}}") + "}"


def connect():
    server = os.environ["AUDIT_SQL_SERVER"]
    if "." not in server:
        server += ".database.windows.net"
    conn_str = (
        f"DRIVER={{{os.environ.get('AUDIT_SQL_DRIVER', 'ODBC Driver 17 for SQL Server')}}};"
        f"SERVER=tcp:{server},1433;"
        f"DATABASE={os.environ['AUDIT_SQL_DATABASE']};"
        f"UID={_odbc_escape(os.environ['AUDIT_SQL_USER'])};"
        f"PWD={_odbc_escape(os.environ['AUDIT_SQL_PASSWORD'])};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str, autocommit=True)


def hash_password(password, salt_hex=None, iterations=PBKDF2_ITERATIONS):
    """Returns (hash_hex, salt_hex, iterations)."""
    salt_hex = salt_hex or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
    return digest.hex(), salt_hex, iterations


def parse_basic_auth(header):
    """'Basic base64(user:pass)' -> (user, pass), or (None, None) if absent/malformed."""
    if not header or not header.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None, None
    username, sep, password = decoded.partition(":")
    if not sep or not username:
        return None, None
    return username, password


def _check_password(cursor, username, password):
    row = None
    if username is not None:
        cursor.execute(
            "SELECT password_hash, salt, iterations FROM dbo.ScalerUsers WHERE username = ? AND is_active = 1",
            username,
        )
        row = cursor.fetchone()
    stored_hash, salt, iterations = row if row else (_DUMMY_HASH, _DUMMY_SALT, PBKDF2_ITERATIONS)
    candidate, _, _ = hash_password(password or "", salt, iterations)
    return row is not None and hmac.compare_digest(candidate, stored_hash)


def begin_invocation(username, password, client_ip, user_agent, scale, de, web):
    """Checks the login and writes the audit row. Returns (auth_ok, audit_id).
    Raises if the database is unreachable - callers must then refuse the request."""
    with connect() as conn:
        cursor = conn.cursor()
        auth_ok = _check_password(cursor, username, password)
        cursor.execute(
            "INSERT INTO dbo.ScalerAuditLog (username, auth_ok, client_ip, user_agent, scale, de, web) "
            "OUTPUT INSERTED.id VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username or "")[:100] or None,
            auth_ok,
            (client_ip or "")[:64] or None,
            (user_agent or "")[:400] or None,
            (scale or "")[:20] or None,
            de,
            web,
        )
        audit_id = cursor.fetchone()[0]
    return auth_ok, audit_id


def finish_invocation(audit_id, http_status, result_json, duration_ms):
    with connect() as conn:
        conn.cursor().execute(
            "UPDATE dbo.ScalerAuditLog SET finished_at_utc = SYSUTCDATETIME(), http_status = ?, "
            "duration_ms = ?, result_json = ? WHERE id = ?",
            http_status,
            duration_ms,
            result_json,
            audit_id,
        )


def upsert_user(username, password):
    password_hash, salt, iterations = hash_password(password)
    with connect() as conn:
        conn.cursor().execute(
            "MERGE dbo.ScalerUsers AS t USING (SELECT ? AS username) AS s ON t.username = s.username "
            "WHEN MATCHED THEN UPDATE SET password_hash = ?, salt = ?, iterations = ?, is_active = 1 "
            "WHEN NOT MATCHED THEN INSERT (username, password_hash, salt, iterations) VALUES (?, ?, ?, ?);",
            username, password_hash, salt, iterations, username, password_hash, salt, iterations,
        )


def set_user_active(username, active):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE dbo.ScalerUsers SET is_active = ? WHERE username = ?", 1 if active else 0, username)
        return cursor.rowcount

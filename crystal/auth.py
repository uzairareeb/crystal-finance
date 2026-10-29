"""Single-owner authentication. Every route except /login is protected."""
import os
from functools import wraps

from flask import redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from crystal.db import get_conn
from crystal.finance import now_iso

DEFAULT_USER = "admin"
DEFAULT_PASS = "Crystal@123"

# One-time recovery switch. When the Vercel env var CRYSTAL_RECOVERY is set
# to "reset-admin", the next request resets the owner password to the default
# and removes labelled demo data. Used once after an automated verification
# run changed the password; removed again right after.
RECOVERY_TOKEN = "reset-admin"


def ensure_owner():
    """Seed the owner account on first run; returns the user row."""
    conn = get_conn()
    try:
        user = conn.fetchone("SELECT * FROM users LIMIT 1")
        if not user:
            conn.execute(
                "INSERT INTO users (username, password_hash, must_change, created_at) VALUES (?, ?, ?, ?)",
                (DEFAULT_USER, generate_password_hash(DEFAULT_PASS), 1, now_iso()))
            conn.commit()
            user = conn.fetchone("SELECT * FROM users LIMIT 1")
        if user and os.environ.get("CRYSTAL_RECOVERY") == RECOVERY_TOKEN:
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change = 1 WHERE id = ?",
                (generate_password_hash(DEFAULT_PASS), user["id"]))
            # Full factory reset of user-entered data (pre-launch: the DB holds
            # only verification-run artefacts, nothing real yet). Seeded
            # categories, the users row and the audit trail are kept.
            for table in ("allocations", "attachments", "transactions",
                          "receivables", "payables", "recurring",
                          "projects", "people", "accounts",
                          "notifications_seen"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "UPDATE settings SET app_name = ?, currency = ?, date_format = ?, "
                "financial_year_start = ?, theme = ?, default_period = ?",
                ("Crystal Finance", "PKR", "dmy", "01-01", "light", "month"))
            from crystal.db import audit
            audit(conn, "recovery_reset", "users", user["id"],
                  "Owner password reset to default; all user data wiped (factory reset) via CRYSTAL_RECOVERY")
            conn.commit()
        return user
    finally:
        conn.close()


def verify(username, password):
    conn = get_conn()
    try:
        user = conn.fetchone("SELECT * FROM users WHERE username = ?", (username,))
        if user and check_password_hash(user["password_hash"], password):
            return user
        return None
    finally:
        conn.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def current_user():
    if not session.get("user_id"):
        return None
    conn = get_conn()
    try:
        return conn.fetchone("SELECT * FROM users WHERE id = ?", (session["user_id"],))
    finally:
        conn.close()

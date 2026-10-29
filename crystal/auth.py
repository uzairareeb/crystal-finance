"""Single-owner authentication. Every route except /login is protected."""
from functools import wraps

from flask import redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from crystal.db import get_conn
from crystal.finance import now_iso

DEFAULT_USER = "admin"
DEFAULT_PASS = "Crystal@123"


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

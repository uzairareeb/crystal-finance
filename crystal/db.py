"""Database layer: SQLite locally, Postgres (DATABASE_URL) in production.

Rules learned from earlier projects:
- NEVER put a literal % sign in SQL text. LIKE patterns are bound as
  parameters (the % lives in the parameter value, which psycopg accepts).
- SQL is written with ? placeholders; for Postgres they are translated
  to %s at execution time.
- All money is INTEGER paisa. All booleans are INTEGER 0/1 (works on
  both engines). Timestamps are ISO TEXT.
"""
import os
import sqlite3

try:
    import psycopg
    _HAS_PSYCOPG = True
except Exception:
    _HAS_PSYCOPG = False


def is_postgres():
    return bool(os.environ.get("DATABASE_URL")) and _HAS_PSYCOPG


def db_path():
    if os.environ.get("CRYSTAL_DB"):
        return os.environ["CRYSTAL_DB"]
    if os.environ.get("VERCEL"):
        return "/tmp/crystal.db"
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "crystal.db")


class Conn:
    """Thin wrapper so callers use one API on both engines."""

    def __init__(self):
        self.pg = is_postgres()
        if self.pg:
            self.conn = psycopg.connect(os.environ["DATABASE_URL"])
        else:
            self.conn = sqlite3.connect(db_path())
            self.conn.row_factory = sqlite3.Row

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.pg else sql

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        return cur

    def fetchall(self, sql, params=()):
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        if self.pg:
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, r)) for r in rows]
        return [dict(r) for r in rows]

    def fetchone(self, sql, params=()):
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def insert(self, sql, params=()):
        """INSERT returning the new id on both engines."""
        if self.pg:
            cur = self.execute(sql + " RETURNING id", params)
            return cur.fetchone()[0]
        cur = self.execute(sql, params)
        return cur.lastrowid

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def get_conn():
    return Conn()


ID_SQLITE = "INTEGER PRIMARY KEY AUTOINCREMENT"
ID_PG = "SERIAL PRIMARY KEY"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id {ID} , username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  must_change INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  id {ID} , app_name TEXT NOT NULL DEFAULT 'Crystal Finance',
  currency TEXT NOT NULL DEFAULT 'PKR', date_format TEXT NOT NULL DEFAULT 'dmy',
  financial_year_start TEXT NOT NULL DEFAULT '01-01', theme TEXT NOT NULL DEFAULT 'light',
  default_period TEXT NOT NULL DEFAULT 'month'
);
CREATE TABLE IF NOT EXISTS accounts (
  id {ID} , name TEXT NOT NULL, type TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'PKR',
  opening_paisa INTEGER NOT NULL DEFAULT 0, opening_date TEXT, notes TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
  id {ID} , name TEXT NOT NULL, entity_type TEXT NOT NULL DEFAULT 'Person',
  phone TEXT DEFAULT '', email TEXT DEFAULT '', org TEXT DEFAULT '',
  notes TEXT DEFAULT '', tags TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
  is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id {ID} , name TEXT NOT NULL, client_id INTEGER, start_date TEXT, expected_paisa INTEGER NOT NULL DEFAULT 0,
  payment_schedule TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'Active', notes TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id {ID} , name TEXT NOT NULL, kind TEXT NOT NULL, budget_paisa INTEGER,
  active INTEGER NOT NULL DEFAULT 1, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
  id {ID} , code TEXT UNIQUE NOT NULL, tx_date TEXT NOT NULL, type TEXT NOT NULL,
  amount_paisa INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'PKR',
  person_id INTEGER, project_id INTEGER, category_id INTEGER, method TEXT DEFAULT '',
  source_account_id INTEGER, dest_account_id INTEGER, status TEXT NOT NULL DEFAULT 'Completed',
  reference TEXT DEFAULT '', invoice_ref TEXT DEFAULT '', description TEXT DEFAULT '',
  notes TEXT DEFAULT '', fee_paisa INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS receivables (
  id {ID} , person_id INTEGER, project_id INTEGER, invoice_ref TEXT DEFAULT '',
  original_paisa INTEGER NOT NULL, due_date TEXT, notes TEXT DEFAULT '',
  cancelled INTEGER NOT NULL DEFAULT 0, is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payables (
  id {ID} , person_id INTEGER, project_id INTEGER, description TEXT DEFAULT '',
  original_paisa INTEGER NOT NULL, due_date TEXT, notes TEXT DEFAULT '',
  cancelled INTEGER NOT NULL DEFAULT 0, is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS allocations (
  id {ID} , kind TEXT NOT NULL, target_id INTEGER NOT NULL,
  transaction_id INTEGER NOT NULL, amount_paisa INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recurring (
  id {ID} , name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'paid',
  amount_paisa INTEGER NOT NULL, frequency TEXT NOT NULL DEFAULT 'monthly',
  start_date TEXT, end_date TEXT, next_due TEXT, person_id INTEGER, category_id INTEGER,
  account_id INTEGER, active INTEGER NOT NULL DEFAULT 1, is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
  id {ID} , owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
  filename TEXT NOT NULL, mime TEXT DEFAULT '', data_b64 TEXT NOT NULL,
  uploaded_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notifications_seen (
  id {ID} , nkey TEXT UNIQUE NOT NULL, seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id {ID} , action TEXT NOT NULL, entity TEXT NOT NULL, entity_id INTEGER,
  detail TEXT DEFAULT '', created_at TEXT NOT NULL
);
"""


def init_db():
    conn = get_conn()
    id_type = ID_PG if conn.pg else ID_SQLITE
    try:
        for stmt in SCHEMA.replace("{ID}", id_type).split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        _seed(conn)
        conn.commit()
    finally:
        conn.close()


def _seed(conn):
    if not conn.fetchone("SELECT id FROM settings LIMIT 1"):
        conn.execute(
            "INSERT INTO settings (app_name, currency) VALUES (?, ?)",
            ("Crystal Finance", "PKR"),
        )
    if not conn.fetchone("SELECT id FROM categories LIMIT 1"):
        income = ["Freelance Development", "SEO Services", "Social Media Marketing",
                  "Client Payments", "Salary", "Other Income"]
        expense = ["Developer Payments", "Contractor Payments", "Software & Subscriptions",
                   "Internet & Utilities", "Personal Expenses", "Business Expenses",
                   "Bank & Transfer Fees", "Other Expenses"]
        for n in income:
            conn.execute("INSERT INTO categories (name, kind) VALUES (?, ?)", (n, "income"))
        for n in expense:
            conn.execute("INSERT INTO categories (name, kind) VALUES (?, ?)", (n, "expense"))


def audit(conn, action, entity, entity_id, detail=""):
    from crystal.finance import now_iso
    conn.execute(
        "INSERT INTO audit_log (action, entity, entity_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (action, entity, entity_id, detail, now_iso()),
    )

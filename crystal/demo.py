"""Labelled demo data. OFF by default; every demo row has is_demo=1 so
Settings -> Clear demo data can remove exactly those rows, nothing else."""
from crystal.db import audit, get_conn
from crystal.finance import now_iso


def load_demo():
    conn = get_conn()
    try:
        if conn.fetchone("SELECT id FROM transactions WHERE is_demo = 1 LIMIT 1"):
            return False
        now = now_iso()
        acc = conn.insert(
            "INSERT INTO accounts (name, type, opening_paisa, opening_date, created_at, updated_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Demo Cash Wallet", "Cash", 5000000, "2026-01-01", now, now, 1))
        per = conn.insert(
            "INSERT INTO people (name, entity_type, created_at, updated_at, is_demo) VALUES (?, ?, ?, ?, ?)",
            ("Demo Client", "Client", now, now, 1))
        cat = conn.fetchone("SELECT id FROM categories WHERE name = ? AND kind = ?", ("Client Payments", "income"))
        conn.execute(
            "INSERT INTO transactions (code, tx_date, type, amount_paisa, person_id, category_id, source_account_id, status, description, created_at, updated_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("CF-9001", "2026-10-15", "received", 10000000, per, cat["id"] if cat else None,
             acc, "Completed", "Demo: sample client payment (demo data)", now, now, 1))
        conn.execute(
            "INSERT INTO receivables (person_id, original_paisa, due_date, notes, created_at, updated_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (per, 10000000, "2026-11-15", "Demo receivable: 100,000 due", now, now, 1))
        conn.commit()
        audit(conn, "demo_load", "demo", 0, "Labelled demo data loaded")
        conn.commit()
        return True
    finally:
        conn.close()


def clear_demo():
    conn = get_conn()
    try:
        for table in ("allocations", "attachments", "transactions", "receivables",
                      "payables", "recurring", "projects", "people", "accounts"):
            if table == "allocations":
                conn.execute("DELETE FROM allocations WHERE transaction_id IN (SELECT id FROM transactions WHERE is_demo = 1)")
            else:
                conn.execute(f"DELETE FROM {table} WHERE is_demo = 1")
        conn.commit()
        audit(conn, "demo_clear", "demo", 0, "Demo data cleared")
        conn.commit()
    finally:
        conn.close()

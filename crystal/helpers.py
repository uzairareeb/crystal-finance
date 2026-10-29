"""Shared route helpers: lookups, pagination, attachment handling."""
import base64

from flask import flash, request

from crystal.db import audit, get_conn
from crystal.finance import fmt_money, now_iso, to_paisa

PAGE_SIZE = 25


def ctx_extra(conn):
    from crystal.finance import notifications
    user_row = conn.fetchone("SELECT * FROM users LIMIT 1")
    return {
        "notif_count": len(notifications(conn)),
        "must_change": bool(user_row and int(user_row["must_change"])),
        "fmt_money": fmt_money,
    }


def lookups(conn):
    return {
        "accounts": conn.fetchall("SELECT * FROM accounts WHERE active = 1 ORDER BY name"),
        "people": conn.fetchall("SELECT * FROM people WHERE active = 1 ORDER BY name"),
        "projects": conn.fetchall("SELECT * FROM projects WHERE active = 1 ORDER BY name"),
        "categories": conn.fetchall("SELECT * FROM categories WHERE active = 1 ORDER BY kind, name"),
        "methods": ["Cash", "Bank Transfer", "JazzCash", "Easypaisa", "Card", "Cheque", "Other"],
    }


def paginate(conn, base_sql, params, page):
    total_row = conn.fetchone(f"SELECT COUNT(*) AS c FROM ({base_sql}) t", params)
    total = int(total_row["c"]) if total_row else 0
    offset = (page - 1) * PAGE_SIZE
    rows = conn.fetchall(base_sql + " LIMIT ? OFFSET ?", params + [PAGE_SIZE, offset])
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return rows, total, pages


def save_attachment(conn, owner_type, owner_id, file_storage):
    if not file_storage or not file_storage.filename:
        return None
    data = file_storage.read()
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("Attachment must be 5 MB or smaller")
    return conn.insert(
        "INSERT INTO attachments (owner_type, owner_id, filename, mime, data_b64, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (owner_type, owner_id, file_storage.filename,
         file_storage.mimetype or "", base64.b64encode(data).decode(), now_iso()))


def parse_tx_form(form):
    """Validate a transaction form; returns (clean_dict, error_or_None)."""
    try:
        amount = to_paisa(form.get("amount", ""))
    except ValueError:
        return None, "Enter a valid amount"
    if amount <= 0 and form.get("type") != "opening_adjustment":
        return None, "Amount must be greater than zero"
    t = form.get("type", "received")
    if t == "transfer" and form.get("source_account_id") == form.get("dest_account_id"):
        return None, "Transfer source and destination accounts must differ"
    if t not in ("received", "paid", "transfer", "refund_received", "refund_issued", "opening_adjustment"):
        return None, "Invalid transaction type"
    if not form.get("tx_date"):
        return None, "Transaction date is required"
    fee = 0
    if form.get("fee"):
        try:
            fee = to_paisa(form["fee"])
        except ValueError:
            return None, "Invalid transfer fee"
    def opt(key):
        v = form.get(key)
        return int(v) if v else None
    return {
        "tx_date": form["tx_date"], "type": t, "amount_paisa": amount,
        "person_id": opt("person_id"), "project_id": opt("project_id"),
        "category_id": opt("category_id"), "method": form.get("method", ""),
        "source_account_id": opt("source_account_id"), "dest_account_id": opt("dest_account_id"),
        "status": form.get("status", "Completed"), "reference": form.get("reference", ""),
        "invoice_ref": form.get("invoice_ref", ""), "description": form.get("description", ""),
        "notes": form.get("notes", ""), "fee_paisa": fee,
    }, None


def flash_and_audit(conn, msg, action, entity, eid, detail=""):
    flash(msg, "ok")
    audit(conn, action, entity, eid, detail)

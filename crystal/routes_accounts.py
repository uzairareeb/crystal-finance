from flask import Blueprint, flash, redirect, render_template, request, url_for

from crystal.auth import login_required
from crystal.db import get_conn
from crystal.finance import (advance_due, enrich_outstanding, fmt_date,
                             next_code, now_iso, to_paisa)
from crystal.helpers import ctx_extra, flash_and_audit, save_attachment

bp = Blueprint("accounts", __name__)


# ---------- Accounts ----------
@bp.route("/accounts", methods=["GET", "POST"])
@login_required
def accounts_list():
    conn = get_conn()
    try:
        if request.method == "POST":
            try:
                opening = to_paisa(request.form.get("opening", "0") or "0")
            except ValueError:
                flash("Invalid opening balance.", "err")
                return redirect(url_for("accounts.accounts_list"))
            now = now_iso()
            aid = conn.insert(
                "INSERT INTO accounts (name, type, opening_paisa, opening_date, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request.form.get("name", "").strip(), request.form.get("type", "Other"),
                 opening, request.form.get("opening_date", ""), request.form.get("notes", ""), now, now))
            flash_and_audit(conn, "Account added.", "create", "account", aid, request.form.get("name", ""))
            conn.commit()
            return redirect(url_for("accounts.accounts_list"))
        from crystal.finance import account_balances
        bal = account_balances(conn)
        rows = conn.fetchall("SELECT * FROM accounts ORDER BY name")
        return render_template("accounts.html", title="My Accounts", rows=rows, bal=bal,
                               **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/accounts/<int:aid>/archive", methods=["POST"])
@login_required
def archive_account(aid):
    conn = get_conn()
    try:
        conn.execute("UPDATE accounts SET active = 0, updated_at = ? WHERE id = ?", (now_iso(), aid))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("accounts.accounts_list"))


# ---------- Receivables ----------
def _outstanding_page(table, title, template, form_fields):
    conn = get_conn()
    try:
        if request.method == "POST":
            try:
                amt = to_paisa(request.form.get("original", ""))
            except ValueError:
                flash("Invalid amount.", "err")
                return redirect(request.path)
            now = now_iso()
            person = request.form.get("person_id")
            project = request.form.get("project_id")
            if table == "receivables":
                rid = conn.insert(
                    "INSERT INTO receivables (person_id, project_id, invoice_ref, original_paisa, due_date, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (int(person) if person else None, int(project) if project else None,
                     request.form.get("invoice_ref", ""), amt, request.form.get("due_date", ""),
                     request.form.get("notes", ""), now, now))
            else:
                rid = conn.insert(
                    "INSERT INTO payables (person_id, project_id, description, original_paisa, due_date, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (int(person) if person else None, int(project) if project else None,
                     request.form.get("description", ""), amt, request.form.get("due_date", ""),
                     request.form.get("notes", ""), now, now))
            try:
                save_attachment(conn, table[:-1], rid, request.files.get("attachment"))
            except ValueError as e:
                flash(str(e), "err")
            flash_and_audit(conn, f"{title[:-1]} added.", "create", table[:-1], rid, "")
            conn.commit()
            return redirect(request.path)
        rows = enrich_outstanding(conn, table, conn.fetchall(f"SELECT * FROM {table} ORDER BY due_date"))
        people = conn.fetchall("SELECT * FROM people ORDER BY name")
        projects = conn.fetchall("SELECT * FROM projects ORDER BY name")
        pmap = {p["id"]: p["name"] for p in conn.fetchall("SELECT id, name FROM people")}
        return render_template(template, title=title, table=table, rows=rows, people=people,
                               projects=projects, pmap=pmap, fmt_date=fmt_date, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/receive", methods=["GET", "POST"])
@login_required
def receivables():
    return _outstanding_page("receivables", "Money to Receive", "outstanding.html", None)


@bp.route("/pay", methods=["GET", "POST"])
@login_required
def payables():
    return _outstanding_page("payables", "Money to Pay", "outstanding.html", None)


@bp.route("/receive/<int:rid>/cancel", methods=["POST"])
@login_required
def cancel_receivable(rid):
    return _cancel("receivables", rid, "receive")


@bp.route("/pay/<int:pid>/cancel", methods=["POST"])
@login_required
def cancel_payable(pid):
    return _cancel("payables", pid, "pay")


def _cancel(table, rid, endpoint):
    conn = get_conn()
    try:
        conn.execute(f"UPDATE {table} SET cancelled = 1, updated_at = ? WHERE id = ?", (now_iso(), rid))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for(f"accounts.{endpoint}"))


@bp.route("/receive/<int:rid>/pay", methods=["GET", "POST"])
@login_required
def pay_receivable(rid):
    return _record_allocation("receivables", rid, "received")


@bp.route("/pay/<int:pid>/pay", methods=["GET", "POST"])
@login_required
def pay_payable(pid):
    return _record_allocation("payables", pid, "paid")


def _record_allocation(table, rid, tx_type):
    """Create a Completed transaction allocated to this item (partial ok)."""
    conn = get_conn()
    try:
        row = conn.fetchone(f"SELECT * FROM {table} WHERE id = ?", (rid,))
        if not row:
            flash("Record not found.", "err")
            return redirect(url_for("accounts.receivables" if table == "receivables" else "accounts.payables"))
        from crystal.finance import outstanding
        rem = outstanding(conn, table, rid)
        if request.method == "POST":
            try:
                amt = to_paisa(request.form.get("amount", ""))
            except ValueError:
                flash("Invalid amount.", "err")
                return redirect(request.path)
            if amt <= 0 or amt > rem:
                flash(f"Amount must be between 1 and the outstanding {rem // 100:,}.", "err")
                return redirect(request.path)
            account = request.form.get("account_id")
            if not account:
                flash("Choose an account.", "err")
                return redirect(request.path)
            now = now_iso()
            tid = conn.insert(
                "INSERT INTO transactions (code, tx_date, type, amount_paisa, person_id, project_id, status, description, source_account_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (next_code(conn), request.form.get("tx_date") or now[:10], tx_type, amt,
                 row.get("person_id"), row.get("project_id"), "Completed",
                 request.form.get("description", "") or f"Payment against {table[:-1]} #{rid}",
                 int(account), now, now))
            conn.execute(
                "INSERT INTO allocations (kind, target_id, transaction_id, amount_paisa, created_at) VALUES (?, ?, ?, ?, ?)",
                (table[:-1], rid, tid, amt, now))
            flash_and_audit(conn, "Payment recorded and allocated.", "create", "transaction", tid, "")
            conn.commit()
            return redirect(url_for("tx.detail", tx_id=tid))
        accounts = conn.fetchall("SELECT * FROM accounts WHERE active = 1 ORDER BY name")
        return render_template("allocate.html", title="Record Payment", row=row, rem=rem,
                               accounts=accounts, tx_type=tx_type, fmt_date=fmt_date,
                               **ctx_extra(conn))
    finally:
        conn.close()


# ---------- Recurring ----------
@bp.route("/recurring", methods=["GET", "POST"])
@login_required
def recurring_list():
    conn = get_conn()
    try:
        if request.method == "POST":
            try:
                amt = to_paisa(request.form.get("amount", ""))
            except ValueError:
                flash("Invalid amount.", "err")
                return redirect(url_for("accounts.recurring_list"))
            now = now_iso()
            start = request.form.get("start_date", "")
            rid = conn.insert(
                "INSERT INTO recurring (name, kind, amount_paisa, frequency, start_date, end_date, next_due, person_id, category_id, account_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request.form.get("name", "").strip(), request.form.get("kind", "paid"), amt,
                 request.form.get("frequency", "monthly"), start, request.form.get("end_date", ""),
                 start, int(request.form["person_id"]) if request.form.get("person_id") else None,
                 int(request.form["category_id"]) if request.form.get("category_id") else None,
                 int(request.form["account_id"]) if request.form.get("account_id") else None, now, now))
            flash_and_audit(conn, "Recurring rule added.", "create", "recurring", rid, "")
            conn.commit()
            return redirect(url_for("accounts.recurring_list"))
        rows = conn.fetchall("SELECT * FROM recurring ORDER BY next_due")
        people = conn.fetchall("SELECT * FROM people ORDER BY name")
        cats = conn.fetchall("SELECT * FROM categories ORDER BY name")
        accts = conn.fetchall("SELECT * FROM accounts ORDER BY name")
        pmap = {p["id"]: p["name"] for p in conn.fetchall("SELECT id, name FROM people")}
        return render_template("recurring.html", title="Recurring Transactions", rows=rows,
                               people=people, cats=cats, accts=accts, pmap=pmap,
                               fmt_date=fmt_date, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/recurring/<int:rid>/toggle", methods=["POST"])
@login_required
def toggle_recurring(rid):
    conn = get_conn()
    try:
        row = conn.fetchone("SELECT active FROM recurring WHERE id = ?", (rid,))
        if row:
            conn.execute("UPDATE recurring SET active = ?, updated_at = ? WHERE id = ?",
                         (0 if int(row["active"]) else 1, now_iso(), rid))
            conn.commit()
    finally:
        conn.close()
    return redirect(url_for("accounts.recurring_list"))


@bp.route("/recurring/<int:rid>/generate", methods=["POST"])
@login_required
def generate_recurring(rid):
    """Create a PENDING draft only, then advance next_due. Never a completed duplicate."""
    conn = get_conn()
    try:
        row = conn.fetchone("SELECT * FROM recurring WHERE id = ?", (rid,))
        if not row:
            flash("Rule not found.", "err")
            return redirect(url_for("accounts.recurring_list"))
        tx_type = "received" if row["kind"] == "received" else "paid"
        now = now_iso()
        tid = conn.insert(
            "INSERT INTO transactions (code, tx_date, type, amount_paisa, person_id, category_id, source_account_id, status, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (next_code(conn), row.get("next_due") or now[:10], tx_type, int(row["amount_paisa"]),
             row.get("person_id"), row.get("category_id"), row.get("account_id"), "Pending",
             f"Recurring draft: {row['name']}", now, now))
        new_due = advance_due(row.get("next_due") or now[:10], row["frequency"])
        conn.execute("UPDATE recurring SET next_due = ?, updated_at = ? WHERE id = ?",
                     (new_due, now, rid))
        flash_and_audit(conn, "Pending draft created. Confirm it when the money actually moves.",
                        "create", "transaction", tid, row["name"])
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("tx.detail", tx_id=tid))

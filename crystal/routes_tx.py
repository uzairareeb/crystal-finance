from flask import Blueprint, flash, redirect, render_template, request, url_for

from crystal.auth import login_required
from crystal.db import audit, get_conn
from crystal.finance import fmt_date, next_code, now_iso
from crystal.helpers import (ctx_extra, flash_and_audit, lookups, paginate,
                             parse_tx_form, save_attachment)

bp = Blueprint("tx", __name__)

TYPE_LABELS = {
    "received": "Money Received", "paid": "Money Paid", "transfer": "Transfer",
    "refund_received": "Refund Received", "refund_issued": "Refund Issued",
    "opening_adjustment": "Opening Balance Adjustment",
}


def _filters():
    f = {}
    for key in ("type", "status", "account", "method", "person", "project", "category"):
        v = request.args.get(key, "")
        if v:
            f[key] = v
    for key in ("from", "to", "min_amount", "max_amount", "q"):
        v = request.args.get(key, "")
        if v:
            f[key] = v
    return f


def _list_query(f, type_filter=None):
    sql = "SELECT * FROM transactions WHERE 1 = 1"
    params = []
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
    if f.get("type"):
        sql += " AND type = ?"
        params.append(f["type"])
    if f.get("status"):
        sql += " AND status = ?"
        params.append(f["status"])
    if f.get("account"):
        sql += " AND (source_account_id = ? OR dest_account_id = ?)"
        params += [int(f["account"]), int(f["account"])]
    if f.get("method"):
        sql += " AND method = ?"
        params.append(f["method"])
    if f.get("person"):
        sql += " AND person_id = ?"
        params.append(int(f["person"]))
    if f.get("project"):
        sql += " AND project_id = ?"
        params.append(int(f["project"]))
    if f.get("category"):
        sql += " AND category_id = ?"
        params.append(int(f["category"]))
    if f.get("from"):
        sql += " AND tx_date >= ?"
        params.append(f["from"])
    if f.get("to"):
        sql += " AND tx_date <= ?"
        params.append(f["to"])
    if f.get("min_amount"):
        sql += " AND amount_paisa >= ?"
        params.append(int(float(f["min_amount"]) * 100))
    if f.get("max_amount"):
        sql += " AND amount_paisa <= ?"
        params.append(int(float(f["max_amount"]) * 100))
    if f.get("q"):
        like = "%" + f["q"] + "%"
        sql += " AND (code LIKE ? OR description LIKE ? OR reference LIKE ? OR invoice_ref LIKE ?)"
        params += [like, like, like, like]
    sort = request.args.get("sort", "date_desc")
    order = {"date_desc": "tx_date DESC, id DESC", "date_asc": "tx_date ASC, id ASC",
             "amount_desc": "amount_paisa DESC", "amount_asc": "amount_paisa ASC",
             "updated": "updated_at DESC"} .get(sort, "tx_date DESC, id DESC")
    return sql + " ORDER BY " + order, params


def _render_list(title, type_filter=None):
    f = _filters()
    page = max(1, int(request.args.get("page", 1) or 1))
    conn = get_conn()
    try:
        sql, params = _list_query(f, type_filter)
        rows, total, pages = paginate(conn, sql, params, page)
        lk = lookups(conn)
        people = {p["id"]: p["name"] for p in conn.fetchall("SELECT id, name FROM people")}
        accts = {a["id"]: a["name"] for a in conn.fetchall("SELECT id, name FROM accounts")}
        cats = {c["id"]: c["name"] for c in conn.fetchall("SELECT id, name FROM categories")}
        return render_template("tx_list.html", title=title, rows=rows, total=total,
                               pages=pages, page=page, f=f, lk=lk, people=people,
                               accts=accts, cats=cats, labels=TYPE_LABELS,
                               fmt_date=fmt_date, sort=request.args.get("sort", "date_desc"),
                               **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/transactions")
@login_required
def all_tx():
    return _render_list("All Transactions")


@bp.route("/transactions/received")
@login_required
def received():
    return _render_list("Money Received", "received")


@bp.route("/transactions/paid")
@login_required
def paid():
    return _render_list("Money Paid", "paid")


@bp.route("/transactions/transfers")
@login_required
def transfers():
    return _render_list("Transfers", "transfer")


@bp.route("/transactions/new", methods=["GET", "POST"])
@login_required
def new_tx():
    conn = get_conn()
    try:
        if request.method == "POST":
            clean, err = parse_tx_form(request.form)
            if err:
                flash(err, "err")
            else:
                now = now_iso()
                tid = conn.insert(
                    "INSERT INTO transactions (code, tx_date, type, amount_paisa, currency, person_id, project_id, category_id, method, source_account_id, dest_account_id, status, reference, invoice_ref, description, notes, fee_paisa, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (next_code(conn), clean["tx_date"], clean["type"], clean["amount_paisa"],
                     "PKR", clean["person_id"], clean["project_id"], clean["category_id"],
                     clean["method"], clean["source_account_id"], clean["dest_account_id"],
                     clean["status"], clean["reference"], clean["invoice_ref"],
                     clean["description"], clean["notes"], clean["fee_paisa"], now, now))
                # Optional allocation to a receivable/payable.
                alloc_kind = request.form.get("allocate_kind")
                alloc_id = request.form.get("allocate_id")
                if alloc_kind in ("receivable", "payable") and alloc_id:
                    table = "receivables" if alloc_kind == "receivable" else "payables"
                    from crystal.finance import outstanding
                    rem = outstanding(conn, table, int(alloc_id))
                    amt = min(int(clean["amount_paisa"]), max(0, rem))
                    if amt > 0 and clean["status"] == "Completed":
                        conn.execute(
                            "INSERT INTO allocations (kind, target_id, transaction_id, amount_paisa, created_at) VALUES (?, ?, ?, ?, ?)",
                            (alloc_kind, int(alloc_id), tid, amt, now))
                try:
                    save_attachment(conn, "transaction", tid, request.files.get("attachment"))
                except ValueError as e:
                    flash(str(e), "err")
                flash_and_audit(conn, "Transaction saved.", "create", "transaction", tid, clean["description"])
                conn.commit()
                return redirect(url_for("tx.detail", tx_id=tid))
        lk = lookups(conn)
        return render_template("tx_form.html", title="Add Transaction", tx=None, lk=lk,
                               labels=TYPE_LABELS, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/transactions/<int:tx_id>")
@login_required
def detail(tx_id):
    conn = get_conn()
    try:
        tx = conn.fetchone("SELECT * FROM transactions WHERE id = ?", (tx_id,))
        if not tx:
            flash("Transaction not found.", "err")
            return redirect(url_for("tx.all_tx"))
        def name(table, _id):
            if not _id:
                return "—"
            r = conn.fetchone(f"SELECT name FROM {table} WHERE id = ?", (_id,))
            return r["name"] if r else "—"
        allocs = conn.fetchall("SELECT * FROM allocations WHERE transaction_id = ?", (tx_id,))
        atts = conn.fetchall("SELECT * FROM attachments WHERE owner_type = ? AND owner_id = ?", ("transaction", tx_id))
        return render_template("tx_detail.html", title=f"Transaction {tx['code']}", tx=tx,
                               person=name("people", tx.get("person_id")),
                               project=name("projects", tx.get("project_id")),
                               category=name("categories", tx.get("category_id")),
                               source=name("accounts", tx.get("source_account_id")),
                               dest=name("accounts", tx.get("dest_account_id")),
                               labels=TYPE_LABELS, allocs=allocs, atts=atts,
                               fmt_date=fmt_date, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/transactions/<int:tx_id>/edit", methods=["GET", "POST"])
@login_required
def edit_tx(tx_id):
    conn = get_conn()
    try:
        tx = conn.fetchone("SELECT * FROM transactions WHERE id = ?", (tx_id,))
        if not tx:
            flash("Transaction not found.", "err")
            return redirect(url_for("tx.all_tx"))
        if request.method == "POST":
            clean, err = parse_tx_form(request.form)
            if err:
                flash(err, "err")
            else:
                conn.execute(
                    "UPDATE transactions SET tx_date = ?, type = ?, amount_paisa = ?, person_id = ?, project_id = ?, category_id = ?, method = ?, source_account_id = ?, dest_account_id = ?, status = ?, reference = ?, invoice_ref = ?, description = ?, notes = ?, fee_paisa = ?, updated_at = ? WHERE id = ?",
                    (clean["tx_date"], clean["type"], clean["amount_paisa"], clean["person_id"],
                     clean["project_id"], clean["category_id"], clean["method"],
                     clean["source_account_id"], clean["dest_account_id"], clean["status"],
                     clean["reference"], clean["invoice_ref"], clean["description"],
                     clean["notes"], clean["fee_paisa"], now_iso(), tx_id))
                try:
                    save_attachment(conn, "transaction", tx_id, request.files.get("attachment"))
                except ValueError as e:
                    flash(str(e), "err")
                flash_and_audit(conn, "Transaction updated.", "update", "transaction", tx_id, "")
                conn.commit()
                return redirect(url_for("tx.detail", tx_id=tx_id))
        return render_template("tx_form.html", title="Edit Transaction", tx=tx,
                               lk=lookups(conn), labels=TYPE_LABELS, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/transactions/<int:tx_id>/void", methods=["POST"])
@login_required
def void_tx(tx_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE transactions SET status = ?, updated_at = ? WHERE id = ?",
                     ("Voided", now_iso(), tx_id))
        flash_and_audit(conn, "Transaction voided. It no longer affects any total.",
                        "void", "transaction", tx_id, "")
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("tx.detail", tx_id=tx_id))


@bp.route("/transactions/<int:tx_id>/restore", methods=["POST"])
@login_required
def restore_tx(tx_id):
    conn = get_conn()
    try:
        conn.execute("UPDATE transactions SET status = ?, updated_at = ? WHERE id = ?",
                     ("Pending", now_iso(), tx_id))
        flash_and_audit(conn, "Transaction restored as Pending.", "restore", "transaction", tx_id, "")
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("tx.detail", tx_id=tx_id))


@bp.route("/transactions/<int:tx_id>/delete", methods=["POST"])
@login_required
def delete_tx(tx_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM allocations WHERE transaction_id = ?", (tx_id,))
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        flash_and_audit(conn, "Transaction deleted.", "delete", "transaction", tx_id, "")
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("tx.all_tx"))

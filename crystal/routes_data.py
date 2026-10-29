import base64
import csv
import io
import json

from flask import (Blueprint, Response, flash, redirect, render_template,
                   request, send_file, url_for)
from werkzeug.security import generate_password_hash

from crystal.auth import login_required
from crystal.db import audit, get_conn
from crystal.demo import clear_demo, load_demo
from crystal.finance import fmt_date, now_iso

bp = Blueprint("data", __name__)

TABLES = ["accounts", "people", "projects", "categories", "transactions",
          "receivables", "payables", "allocations", "recurring", "attachments"]


@bp.route("/attachments")
@login_required
def attachments_list():
    conn = get_conn()
    try:
        rows = conn.fetchall("SELECT * FROM attachments ORDER BY uploaded_at DESC")
        return render_template("attachments.html", title="Attachments & Receipts",
                               rows=rows, fmt_date=fmt_date,
                               **_ctx(conn))
    finally:
        conn.close()


def _ctx(conn):
    from crystal.helpers import ctx_extra
    return ctx_extra(conn)


@bp.route("/attachments/<int:aid>/download")
@login_required
def attachment_download(aid):
    conn = get_conn()
    try:
        row = conn.fetchone("SELECT * FROM attachments WHERE id = ?", (aid,))
        if not row:
            flash("Attachment not found.", "err")
            return redirect(url_for("data.attachments_list"))
        data = base64.b64decode(row["data_b64"])
        return send_file(io.BytesIO(data), mimetype=row.get("mime") or "application/octet-stream",
                         as_attachment=True, download_name=row["filename"])
    finally:
        conn.close()


@bp.route("/attachments/<int:aid>/delete", methods=["POST"])
@login_required
def attachment_delete(aid):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM attachments WHERE id = ?", (aid,))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("data.attachments_list"))


@bp.route("/export", methods=["GET", "POST"])
@login_required
def export_page():
    conn = get_conn()
    try:
        if request.method == "POST":
            kind = request.form.get("kind", "csv")
            if kind == "backup":
                return _backup_response(conn)
            if kind == "restore":
                return _restore(conn)
            return _csv_response(conn, request.form)
        return render_template("export.html", title="Export & Backup", **_ctx(conn))
    finally:
        conn.close()


def _csv_response(conn, form):
    sql = "SELECT * FROM transactions WHERE 1 = 1"
    params = []
    if form.get("from"):
        sql += " AND tx_date >= ?"; params.append(form["from"])
    if form.get("to"):
        sql += " AND tx_date <= ?"; params.append(form["to"])
    if form.get("type"):
        sql += " AND type = ?"; params.append(form["type"])
    if form.get("status"):
        sql += " AND status = ?"; params.append(form["status"])
    rows = conn.fetchall(sql + " ORDER BY tx_date, id", params)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["code", "date", "type", "amount_pkr", "person_id", "project_id",
                "category_id", "method", "source_account_id", "dest_account_id",
                "status", "reference", "invoice_ref", "description"])
    for r in rows:
        w.writerow([r["code"], r["tx_date"], r["type"], int(r["amount_paisa"]) / 100,
                    r.get("person_id"), r.get("project_id"), r.get("category_id"),
                    r.get("method"), r.get("source_account_id"), r.get("dest_account_id"),
                    r["status"], r.get("reference"), r.get("invoice_ref"), r.get("description")])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=crystal-transactions.csv"})


def _backup_response(conn):
    data = {}
    for table in TABLES:
        data[table] = conn.fetchall(f"SELECT * FROM {table}")
    payload = json.dumps(data, indent=2, default=str)
    return Response(payload, mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=crystal-backup.json"})


def _restore(conn):
    f = request.files.get("backup")
    if not f:
        flash("Choose a backup file.", "err")
        return redirect(url_for("data.export_page"))
    try:
        data = json.load(f)
    except Exception:
        flash("Invalid backup file (not valid JSON).", "err")
        return redirect(url_for("data.export_page"))
    existing_codes = {r["code"] for r in conn.fetchall("SELECT code FROM transactions")}
    skipped = added = 0
    for row in data.get("transactions", []):
        if row.get("code") in existing_codes:
            skipped += 1
            continue
        cols = [k for k in row if k != "id"]
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(f"INSERT INTO transactions ({', '.join(cols)}) VALUES ({placeholders})",
                     [row[c] for c in cols])
        added += 1
    conn.commit()
    audit(conn, "restore", "backup", 0, f"Added {added}, skipped {skipped} duplicates by code")
    conn.commit()
    flash(f"Restore complete: {added} added, {skipped} duplicates skipped (never overwritten).", "ok")
    return redirect(url_for("data.export_page"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    conn = get_conn()
    try:
        if request.method == "POST":
            action = request.form.get("action", "general")
            if action == "general":
                conn.execute("UPDATE settings SET app_name = ?, currency = ?, theme = ?, default_period = ? WHERE id = ?",
                             (request.form.get("app_name", "Crystal Finance"), request.form.get("currency", "PKR"),
                              request.form.get("theme", "light"), request.form.get("default_period", "month"),
                              int(request.form.get("settings_id", 1))))
                flash("Settings saved.", "ok")
            elif action == "password":
                from flask import session
                new = request.form.get("new_password", "")
                if len(new) < 8:
                    flash("New password must be at least 8 characters.", "err")
                else:
                    conn.execute("UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?",
                                 (generate_password_hash(new), session.get("user_id")))
                    flash("Password changed.", "ok")
            elif action == "demo_load":
                load_demo()
                flash("Labelled demo data loaded.", "ok")
            elif action == "demo_clear":
                clear_demo()
                flash("Demo data cleared.", "ok")
            conn.commit()
            return redirect(url_for("data.settings"))
        settings = conn.fetchone("SELECT * FROM settings LIMIT 1")
        demo_on = bool(conn.fetchone("SELECT id FROM transactions WHERE is_demo = 1 LIMIT 1"))
        logs = conn.fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT 50")
        return render_template("settings.html", title="Settings", settings=settings,
                               demo_on=demo_on, logs=logs, fmt_date=fmt_date, **_ctx(conn))
    finally:
        conn.close()


@bp.route("/trash")
@login_required
def trash():
    conn = get_conn()
    try:
        voided = conn.fetchall("SELECT * FROM transactions WHERE status = ? ORDER BY updated_at DESC", ("Voided",))
        archived_people = conn.fetchall("SELECT * FROM people WHERE active = 0 ORDER BY name")
        archived_accounts = conn.fetchall("SELECT * FROM accounts WHERE active = 0 ORDER BY name")
        return render_template("trash.html", title="Trash & Recovery", voided=voided,
                               archived_people=archived_people, archived_accounts=archived_accounts,
                               fmt_date=fmt_date, **_ctx(conn))
    finally:
        conn.close()

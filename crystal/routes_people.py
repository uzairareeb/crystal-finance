from flask import Blueprint, flash, redirect, render_template, request, url_for

from crystal.auth import login_required
from crystal.db import get_conn
from crystal.finance import (CASH_IN, CASH_OUT, fmt_date, now_iso,
                             totals_outstanding)
from crystal.helpers import ctx_extra, flash_and_audit, save_attachment

bp = Blueprint("people", __name__)


# ---------- People ----------
@bp.route("/people", methods=["GET", "POST"])
@login_required
def people_list():
    conn = get_conn()
    try:
        if request.method == "POST":
            now = now_iso()
            pid = conn.insert(
                "INSERT INTO people (name, entity_type, phone, email, org, notes, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request.form.get("name", "").strip(), request.form.get("entity_type", "Person"),
                 request.form.get("phone", ""), request.form.get("email", ""), request.form.get("org", ""),
                 request.form.get("notes", ""), request.form.get("tags", ""), now, now))
            flash_and_audit(conn, "Person added.", "create", "person", pid, request.form.get("name", ""))
            conn.commit()
            return redirect(url_for("people.people_list"))
        show = request.args.get("show", "active")
        sql = "SELECT * FROM people" + (" WHERE active = 1" if show == "active" else " WHERE active = 0") + " ORDER BY name"
        rows = conn.fetchall(sql)
        return render_template("people.html", title="People & Companies", rows=rows,
                               show=show, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/people/<int:pid>/archive", methods=["POST"])
@login_required
def archive_person(pid):
    conn = get_conn()
    try:
        conn.execute("UPDATE people SET active = 0, updated_at = ? WHERE id = ?", (now_iso(), pid))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("people.people_list"))


@bp.route("/people/<int:pid>/unarchive", methods=["POST"])
@login_required
def unarchive_person(pid):
    conn = get_conn()
    try:
        conn.execute("UPDATE people SET active = 1, updated_at = ? WHERE id = ?", (now_iso(), pid))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("people.people_list", show="archived"))


@bp.route("/people/<int:pid>")
@login_required
def statement(pid):
    conn = get_conn()
    try:
        person = conn.fetchone("SELECT * FROM people WHERE id = ?", (pid,))
        if not person:
            flash("Person not found.", "err")
            return redirect(url_for("people.people_list"))
        f_from, f_to, f_type = request.args.get("from", ""), request.args.get("to", ""), request.args.get("type", "")
        sql = "SELECT * FROM transactions WHERE person_id = ?"
        params = [pid]
        if f_from:
            sql += " AND tx_date >= ?"; params.append(f_from)
        if f_to:
            sql += " AND tx_date <= ?"; params.append(f_to)
        if f_type:
            sql += " AND type = ?"; params.append(f_type)
        txs = conn.fetchall(sql + " ORDER BY tx_date DESC, id DESC", params)
        received = sum(int(t["amount_paisa"]) for t in txs if t["status"] == "Completed" and t["type"] in CASH_IN)
        paid = sum(int(t["amount_paisa"]) for t in txs if t["status"] == "Completed" and t["type"] in CASH_OUT)
        from crystal.finance import enrich_outstanding
        rec = enrich_outstanding(conn, "receivables", conn.fetchall("SELECT * FROM receivables WHERE person_id = ? AND cancelled = 0", (pid,)))
        pay = enrich_outstanding(conn, "payables", conn.fetchall("SELECT * FROM payables WHERE person_id = ? AND cancelled = 0", (pid,)))
        owes_me = sum(max(0, r["outstanding_paisa"]) for r in rec)
        i_owe = sum(max(0, r["outstanding_paisa"]) for r in pay)
        projects = conn.fetchall("SELECT * FROM projects WHERE client_id = ?", (pid,))
        return render_template("person_statement.html", title=f"{person['name']} — Statement",
                               person=person, txs=txs, received=received, paid=paid,
                               owes_me=owes_me, i_owe=i_owe, rec=rec, pay=pay,
                               projects=projects, fmt_date=fmt_date, f_from=f_from,
                               f_to=f_to, f_type=f_type, **ctx_extra(conn))
    finally:
        conn.close()


# ---------- Projects ----------
@bp.route("/projects", methods=["GET", "POST"])
@login_required
def projects_list():
    conn = get_conn()
    try:
        if request.method == "POST":
            from crystal.finance import to_paisa
            try:
                expected = to_paisa(request.form.get("expected", "0") or "0")
            except ValueError:
                expected = 0
            now = now_iso()
            client = request.form.get("client_id")
            pid = conn.insert(
                "INSERT INTO projects (name, client_id, start_date, expected_paisa, payment_schedule, status, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request.form.get("name", "").strip(), int(client) if client else None,
                 request.form.get("start_date", ""), expected, request.form.get("payment_schedule", ""),
                 request.form.get("status", "Active"), request.form.get("notes", ""), now, now))
            flash_and_audit(conn, "Project added.", "create", "project", pid, request.form.get("name", ""))
            conn.commit()
            return redirect(url_for("people.projects_list"))
        people = conn.fetchall("SELECT * FROM people ORDER BY name")
        rows = conn.fetchall("SELECT * FROM projects ORDER BY name")
        enriched = []
        for r in dict_rows(rows):
            enriched.append(_project_numbers(conn, r))
        return render_template("projects.html", title="Projects & Clients", rows=enriched,
                               people=people, **ctx_extra(conn))
    finally:
        conn.close()


def dict_rows(rows):
    return [dict(r) for r in rows]


def _project_numbers(conn, r):
    txs = conn.fetchall("SELECT * FROM transactions WHERE project_id = ? AND status = ?", (r["id"], "Completed"))
    received = sum(int(t["amount_paisa"]) for t in txs if t["type"] in CASH_IN)
    paid = sum(int(t["amount_paisa"]) for t in txs if t["type"] in CASH_OUT)
    r["received_paisa"] = received
    r["paid_paisa"] = paid
    r["cash_profit"] = received - paid
    r["remaining_paisa"] = int(r["expected_paisa"]) - received
    return r


@bp.route("/projects/<int:pid>")
@login_required
def project_statement(pid):
    conn = get_conn()
    try:
        proj = conn.fetchone("SELECT * FROM projects WHERE id = ?", (pid,))
        if not proj:
            flash("Project not found.", "err")
            return redirect(url_for("people.projects_list"))
        proj = _project_numbers(conn, dict(proj))
        txs = conn.fetchall("SELECT * FROM transactions WHERE project_id = ? ORDER BY tx_date DESC, id DESC", (pid,))
        from crystal.finance import enrich_outstanding
        rec = enrich_outstanding(conn, "receivables", conn.fetchall("SELECT * FROM receivables WHERE project_id = ? AND cancelled = 0", (pid,)))
        pay = enrich_outstanding(conn, "payables", conn.fetchall("SELECT * FROM payables WHERE project_id = ? AND cancelled = 0", (pid,)))
        client = conn.fetchone("SELECT name FROM people WHERE id = ?", (proj.get("client_id"),)) if proj.get("client_id") else None
        return render_template("project_statement.html", title=f"{proj['name']} — Statement",
                               proj=proj, txs=txs, rec=rec, pay=pay,
                               client=client["name"] if client else "—",
                               fmt_date=fmt_date, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/projects/<int:pid>/status", methods=["POST"])
@login_required
def project_status(pid):
    conn = get_conn()
    try:
        conn.execute("UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                     (request.form.get("status", "Active"), now_iso(), pid))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("people.project_statement", pid=pid))

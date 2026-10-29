from datetime import date

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from crystal.auth import login_required
from crystal.db import get_conn
from crystal.finance import (breakdown, chart_series, dashboard, fmt_date,
                             fmt_money, month_bounds, notifications,
                             totals_outstanding)
from crystal.helpers import ctx_extra

bp = Blueprint("main", __name__)


def _period():
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month
    month = min(12, max(1, month))
    return year, month


@bp.route("/")
@login_required
def index():
    return redirect(url_for("main.dashboard_page"))


@bp.route("/dashboard")
@login_required
def dashboard_page():
    year, month = _period()
    grain = request.args.get("grain", "monthly")
    conn = get_conn()
    try:
        d = dashboard(conn, year, month)
        start, end = month_bounds(year, month)
        accounts = conn.fetchall("SELECT * FROM accounts ORDER BY name")
        bal = d["balances"]
        recent = conn.fetchall(
            "SELECT * FROM transactions ORDER BY tx_date DESC, id DESC LIMIT 10")
        from crystal.finance import enrich_outstanding
        rec = enrich_outstanding(conn, "receivables",
                                 conn.fetchall("SELECT * FROM receivables WHERE cancelled = 0 ORDER BY due_date"))
        pay = enrich_outstanding(conn, "payables",
                                 conn.fetchall("SELECT * FROM payables WHERE cancelled = 0 ORDER BY due_date"))
        overdue = [r for r in rec + pay if r["computed_status"] == "Overdue"]
        upcoming = [r for r in rec + pay if r["computed_status"] in ("Due", "Upcoming")][:8]
        income_src = breakdown(conn, start, end, "person")
        exp_cat = breakdown(conn, start, end, "category")
        people = {p["id"]: p["name"] for p in conn.fetchall("SELECT id, name FROM people")}
        cats = {c["id"]: c["name"] for c in conn.fetchall("SELECT id, name FROM categories")}
        series = chart_series(conn, start, end, grain)
        ctx = ctx_extra(conn)
        return render_template(
            "dashboard.html", title="Dashboard", year=year, month=month, grain=grain,
            d=d, accounts=accounts, bal=bal, recent=recent, overdue=overdue,
            upcoming=upcoming, income_src=income_src, exp_cat=exp_cat,
            people=people, cats=cats, series=series, fmt_date=fmt_date, **ctx)
    finally:
        conn.close()


@bp.route("/api/chart")
@login_required
def api_chart():
    year, month = _period()
    grain = request.args.get("grain", "monthly")
    start, end = month_bounds(year, month)
    conn = get_conn()
    try:
        return jsonify(chart_series(conn, start, end, grain))
    finally:
        conn.close()


@bp.route("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    conn = get_conn()
    try:
        results = []
        if q:
            like = "%" + q + "%"
            rows = conn.fetchall(
                "SELECT * FROM transactions WHERE code LIKE ? OR description LIKE ? OR reference LIKE ? OR invoice_ref LIKE ? ORDER BY tx_date DESC, id DESC LIMIT 50",
                (like, like, like, like))
            results = rows
            people = conn.fetchall("SELECT * FROM people WHERE name LIKE ? ORDER BY name LIMIT 20", (like,))
            projects = conn.fetchall("SELECT * FROM projects WHERE name LIKE ? ORDER BY name LIMIT 20", (like,))
        else:
            people, projects = [], []
        return render_template("search.html", title="Search", q=q, results=results,
                               people=people, projects=projects, fmt_date=fmt_date, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/notifications", methods=["GET", "POST"])
@login_required
def notifications_page():
    conn = get_conn()
    try:
        if request.method == "POST":
            key = request.form.get("key", "")
            if key and not conn.fetchone("SELECT id FROM notifications_seen WHERE nkey = ?", (key,)):
                from crystal.finance import now_iso
                conn.execute("INSERT INTO notifications_seen (nkey, seen_at) VALUES (?, ?)",
                             (key, now_iso()))
                conn.commit()
            return redirect(url_for("main.notifications_page"))
        items = notifications(conn)
        return render_template("notifications.html", title="Notifications", items=items,
                               **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/schedule")
@login_required
def schedule():
    conn = get_conn()
    try:
        from crystal.finance import enrich_outstanding
        rec = enrich_outstanding(conn, "receivables",
                                 conn.fetchall("SELECT * FROM receivables WHERE cancelled = 0 ORDER BY due_date"))
        pay = enrich_outstanding(conn, "payables",
                                 conn.fetchall("SELECT * FROM payables WHERE cancelled = 0 ORDER BY due_date"))
        recs = conn.fetchall("SELECT * FROM recurring WHERE active = 1 ORDER BY next_due")
        people = {p["id"]: p["name"] for p in conn.fetchall("SELECT id, name FROM people")}
        return render_template("schedule.html", title="Payment Schedule", rec=rec, pay=pay,
                               recs=recs, people=people, fmt_date=fmt_date, **ctx_extra(conn))
    finally:
        conn.close()

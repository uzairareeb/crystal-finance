import csv
import io

from flask import Blueprint, Response, redirect, render_template, request, url_for, flash

from crystal.auth import login_required
from crystal.db import get_conn
from crystal.finance import (breakdown, chart_series, fmt_date, month_bounds,
                             monthly_report, totals)
from crystal.helpers import ctx_extra, flash_and_audit
from crystal.finance import now_iso, to_paisa

bp = Blueprint("reports", __name__)


def _ym():
    from datetime import date
    t = date.today()
    try:
        return int(request.args.get("year", t.year)), int(request.args.get("month", t.month))
    except ValueError:
        return t.year, t.month


@bp.route("/reports/monthly")
@login_required
def monthly():
    year, month = _ym()
    conn = get_conn()
    try:
        rep = monthly_report(conn, year, month)
        start, end = month_bounds(year, month)
        income_src = breakdown(conn, start, end, "person")
        exp_cat = breakdown(conn, start, end, "category")
        people = {p["id"]: p["name"] for p in conn.fetchall("SELECT id, name FROM people")}
        cats = {c["id"]: c["name"] for c in conn.fetchall("SELECT id, name FROM categories")}
        # Client / project summaries for the month.
        proj_rows = []
        for p in conn.fetchall("SELECT * FROM projects"):
            txs = conn.fetchall("SELECT * FROM transactions WHERE project_id = ? AND status = ? AND tx_date >= ? AND tx_date <= ?",
                                (p["id"], "Completed", start, end))
            from crystal.finance import CASH_IN, CASH_OUT
            r = sum(int(t["amount_paisa"]) for t in txs if t["type"] in CASH_IN)
            pd = sum(int(t["amount_paisa"]) for t in txs if t["type"] in CASH_OUT)
            if r or pd:
                proj_rows.append({"name": p["name"], "received": r, "paid": pd})
        return render_template("report_monthly.html", title="Monthly Report", year=year,
                               month=month, rep=rep, income_src=income_src, exp_cat=exp_cat,
                               people=people, cats=cats, proj_rows=proj_rows,
                               fmt_date=fmt_date, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/reports/annual")
@login_required
def annual():
    from datetime import date
    try:
        year = int(request.args.get("year", date.today().year))
    except ValueError:
        year = date.today().year
    conn = get_conn()
    try:
        months = []
        total_r = total_p = 0
        for m in range(1, 13):
            rep = monthly_report(conn, year, m)
            months.append({"month": m, "received": rep["received"], "paid": rep["paid"],
                           "net": rep["net"], "closing": rep["closing"]})
            total_r += rep["received"]
            total_p += rep["paid"]
        start, end = f"{year}-01-01", f"{year}-12-31"
        from crystal.finance import totals_outstanding
        out = totals_outstanding(conn)
        return render_template("report_annual.html", title="Annual Report", year=year,
                               months=months, total_r=total_r, total_p=total_p,
                               net=total_r - total_p, out=out, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/analytics")
@login_required
def analytics():
    from datetime import date
    try:
        year = int(request.args.get("year", date.today().year))
    except ValueError:
        year = date.today().year
    conn = get_conn()
    try:
        series = chart_series(conn, f"{year}-01-01", f"{year}-12-31", "monthly")
        start, end = f"{year}-01-01", f"{year}-12-31"
        income_src = breakdown(conn, start, end, "person")
        exp_cat = breakdown(conn, start, end, "category")
        people = {p["id"]: p["name"] for p in conn.fetchall("SELECT id, name FROM people")}
        cats = {c["id"]: c["name"] for c in conn.fetchall("SELECT id, name FROM categories")}
        return render_template("analytics.html", title="Financial Analytics", year=year,
                               series=series, income_src=income_src, exp_cat=exp_cat,
                               people=people, cats=cats, **ctx_extra(conn))
    finally:
        conn.close()


@bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    conn = get_conn()
    try:
        if request.method == "POST":
            action = request.form.get("action", "add")
            if action == "add":
                conn.execute("INSERT INTO categories (name, kind) VALUES (?, ?)",
                             (request.form.get("name", "").strip(), request.form.get("kind", "expense")))
                conn.commit()
                flash("Category added.", "ok")
            elif action == "budget":
                try:
                    budget = to_paisa(request.form.get("budget", "") or "0")
                except ValueError:
                    budget = None
                conn.execute("UPDATE categories SET budget_paisa = ? WHERE id = ?",
                             (budget, int(request.form.get("category_id", 0))))
                conn.commit()
                flash("Budget saved.", "ok")
            elif action == "archive":
                conn.execute("UPDATE categories SET active = 0 WHERE id = ?",
                             (int(request.form.get("category_id", 0)),))
                conn.commit()
            return redirect(url_for("reports.categories"))
        from datetime import date
        t = date.today()
        start, end = month_bounds(t.year, t.month)
        cats = conn.fetchall("SELECT * FROM categories ORDER BY kind, name")
        spent = {}
        for row in conn.fetchall(
                "SELECT category_id, SUM(amount_paisa) AS total FROM transactions WHERE status = ? AND type IN (?, ?) AND tx_date >= ? AND tx_date <= ? GROUP BY category_id",
                ("Completed", "paid", "refund_issued", start, end)):
            spent[row["category_id"]] = int(row["total"])
        return render_template("categories.html", title="Categories & Budgets", cats=cats,
                               spent=spent, **ctx_extra(conn))
    finally:
        conn.close()

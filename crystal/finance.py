"""ALL money math lives here. No route or template computes its own totals.

Money is INTEGER paisa everywhere (PKR x 100). Display formatting only
happens at the edge via fmt_money().
Accounting rules enforced here:
- Only Completed transactions move cash.
- received / refund_received = cash in; paid / refund_issued = cash out.
- transfer moves money between accounts, never income/expense, never
  changes the total (except a separately recorded fee, which is cash out
  from the source account).
- opening_adjustment changes one account only, never income/expense.
- Pending / Cancelled / Voided transactions are excluded from every total.
- Receivables/payables never change cash until a Completed payment is
  allocated to them, and only by the allocated amount.
"""
import calendar
import re
from datetime import date, datetime, timedelta

CASH_IN = ("received", "refund_received")
CASH_OUT = ("paid", "refund_issued")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def today_iso():
    return date.today().isoformat()


def to_paisa(amount_str):
    """Parse '125,000.00' / '125000' / '125000.5' -> integer paisa."""
    if amount_str is None or str(amount_str).strip() == "":
        raise ValueError("Amount is required")
    s = str(amount_str).strip().replace(",", "").replace("PKR", "").strip()
    neg = s.startswith("-")
    s = s.lstrip("+-")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise ValueError("Invalid amount")
    whole, _, frac = s.partition(".")
    paisa = int(whole) * 100 + int((frac + "00")[:2])
    return -paisa if neg else paisa


def fmt_money(paisa, currency="PKR"):
    """PKR 125,000.00 — thousands separators, 2 decimals, sign aware."""
    paisa = int(paisa or 0)
    sign = "-" if paisa < 0 else ""
    v = abs(paisa)
    return f"{currency} {sign}{v // 100:,}.{v % 100:02d}"


def fmt_date(iso):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(iso)


def month_bounds(year, month):
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return first.isoformat(), last.isoformat()


def prev_month(year, month):
    if month == 1:
        return year - 1, 12
    return year, month - 1


def account_effect(tx):
    """Return (source_delta, dest_delta) in paisa for one transaction.

    Zero for anything not Completed. Transfers: -amount from source,
    +amount to dest (fee, if any, is an extra cash-out from source).
    """
    if tx["status"] != "Completed":
        return 0, 0
    amt = int(tx["amount_paisa"])
    fee = int(tx.get("fee_paisa") or 0)
    t = tx["type"]
    if t in CASH_IN:
        return amt, 0
    if t in CASH_OUT:
        return -amt, 0
    if t == "transfer":
        return -amt - fee, amt
    if t == "opening_adjustment":
        return amt, 0
    return 0, 0


def account_balances(conn, as_of=None):
    """{account_id: balance_paisa} from opening + completed ledger only."""
    accounts = conn.fetchall("SELECT id, opening_paisa, opening_date FROM accounts")
    bal = {}
    for a in accounts:
        if as_of and a.get("opening_date") and a["opening_date"] > as_of:
            continue
        bal[a["id"]] = int(a["opening_paisa"])
    sql = "SELECT * FROM transactions WHERE status = ?"
    params = ["Completed"]
    if as_of:
        sql += " AND tx_date <= ?"
        params.append(as_of)
    for tx in conn.fetchall(sql, params):
        src, dst = account_effect(tx)
        if tx.get("source_account_id") in bal:
            bal[tx["source_account_id"]] += src
        if tx.get("dest_account_id") in bal:
            bal[tx["dest_account_id"]] += dst
    return bal


def totals(conn, start=None, end=None, account_id=None):
    """Cash in / out / net for Completed tx in an optional date range."""
    sql = "SELECT * FROM transactions WHERE status = ?"
    params = ["Completed"]
    if start:
        sql += " AND tx_date >= ?"
        params.append(start)
    if end:
        sql += " AND tx_date <= ?"
        params.append(end)
    tin = tout = 0
    for tx in conn.fetchall(sql, params):
        if account_id:
            src, dst = account_effect(tx)
            if tx.get("source_account_id") == account_id:
                delta = src
            elif tx.get("dest_account_id") == account_id:
                delta = dst
            else:
                continue
            if delta > 0:
                tin += delta
            elif delta < 0:
                tout += -delta
            continue
        if tx["type"] in CASH_IN:
            tin += int(tx["amount_paisa"])
        elif tx["type"] in CASH_OUT:
            tout += int(tx["amount_paisa"])
        elif tx["type"] == "transfer" and int(tx.get("fee_paisa") or 0):
            tout += int(tx["fee_paisa"])
    return {"received": tin, "paid": tout, "net": tin - tout}


def period_totals(conn, year, month):
    start, end = month_bounds(year, month)
    return totals(conn, start, end)


def outstanding(conn, table, target_id, as_of=None):
    """Outstanding for one receivable/payable row, at a date if given."""
    kind = "receivable" if table == "receivables" else "payable"
    row = conn.fetchone(f"SELECT * FROM {table} WHERE id = ?", (target_id,))
    if not row:
        return 0
    sql = ("SELECT a.amount_paisa, t.tx_date FROM allocations a "
           "JOIN transactions t ON t.id = a.transaction_id "
           "WHERE a.kind = ? AND a.target_id = ? AND t.status = ?")
    params = [kind, target_id, "Completed"]
    if as_of:
        sql += " AND t.tx_date <= ?"
        params.append(as_of)
    paid = sum(int(r["amount_paisa"]) for r in conn.fetchall(sql, params))
    return int(row["original_paisa"]) - paid


def allocated_total(conn, table, target_id):
    kind = "receivable" if table == "receivables" else "payable"
    rows = conn.fetchall(
        "SELECT a.amount_paisa FROM allocations a JOIN transactions t "
        "ON t.id = a.transaction_id WHERE a.kind = ? AND a.target_id = ? AND t.status = ?",
        (kind, target_id, "Completed"))
    return sum(int(r["amount_paisa"]) for r in rows)


def item_status(row, out, today=None):
    if int(row.get("cancelled") or 0):
        return "Cancelled"
    if out <= 0 and int(row["original_paisa"]) > 0:
        return "Paid"
    if out < int(row["original_paisa"]):
        return "Partially Paid"
    today = today or today_iso()
    due = row.get("due_date") or ""
    if due and due < today:
        return "Overdue"
    if due == today:
        return "Due"
    return "Upcoming"


def enrich_outstanding(conn, table, rows, as_of=None):
    out = []
    for row in rows:
        r = dict(row)
        paid = allocated_total(conn, table, r["id"]) if as_of is None else (
            int(r["original_paisa"]) - outstanding(conn, table, r["id"], as_of))
        rem = int(r["original_paisa"]) - paid
        r["paid_paisa"] = paid
        r["outstanding_paisa"] = rem
        r["computed_status"] = item_status(r, rem)
        out.append(r)
    return out


def totals_outstanding(conn):
    """Total money to receive / to pay right now (not cancelled, unpaid)."""
    to_receive = to_pay = 0
    for r in conn.fetchall("SELECT * FROM receivables WHERE cancelled = 0"):
        to_receive += max(0, outstanding(conn, "receivables", r["id"]))
    for r in conn.fetchall("SELECT * FROM payables WHERE cancelled = 0"):
        to_pay += max(0, outstanding(conn, "payables", r["id"]))
    return {"to_receive": to_receive, "to_pay": to_pay}


def dashboard(conn, year, month):
    start, end = month_bounds(year, month)
    py, pm = prev_month(year, month)
    pstart, pend = month_bounds(py, pm)
    cur = totals(conn, start, end)
    prev = totals(conn, pstart, pend)
    bal = account_balances(conn)
    available = sum(bal.values())
    out = totals_outstanding(conn)
    return {
        "period": cur, "prev": prev, "available": available,
        "balances": bal, "to_receive": out["to_receive"], "to_pay": out["to_pay"],
    }


def monthly_report(conn, year, month):
    """Opening/closing reconciliation for one month."""
    start, end = month_bounds(year, month)
    accounts = conn.fetchall("SELECT * FROM accounts ORDER BY name")
    opening_bal = account_balances(conn, as_of=_day_before(start))
    closing_bal = account_balances(conn, as_of=end)
    cur = totals(conn, start, end)
    opening = sum(opening_bal.values())
    closing = sum(closing_bal.values())
    # Outstanding AT period end (historical, not current state).
    rec_end = pay_end = 0
    for r in conn.fetchall("SELECT * FROM receivables WHERE cancelled = 0"):
        rec_end += max(0, outstanding(conn, "receivables", r["id"], as_of=end))
    for r in conn.fetchall("SELECT * FROM payables WHERE cancelled = 0"):
        pay_end += max(0, outstanding(conn, "payables", r["id"], as_of=end))
    return {
        "opening": opening, "closing": closing, "received": cur["received"],
        "paid": cur["paid"], "net": cur["net"],
        "opening_bal": opening_bal, "closing_bal": closing_bal,
        "accounts": accounts, "receivables_end": rec_end, "payables_end": pay_end,
        "start": start, "end": end,
    }


def _day_before(iso):
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def breakdown(conn, start, end, group):
    """Income by source / expense by category within a range."""
    col = "person_id" if group == "person" else "category_id"
    types = CASH_IN if group == "person" else CASH_OUT
    sql = ("SELECT " + col + " AS gid, SUM(amount_paisa) AS total FROM transactions "
           "WHERE status = ? AND tx_date >= ? AND tx_date <= ? AND type IN (?, ?)"
           " GROUP BY " + col + " ORDER BY total DESC")
    # IN-list placeholders are fixed strings, bound as parameters.
    rows = conn.fetchall(sql, ["Completed", start, end, types[0], types[1]])
    return rows


def chart_series(conn, start, end, grain):
    """[{label, received, paid}] for Chart.js, bucketed by grain."""
    sql = ("SELECT tx_date, type, amount_paisa, fee_paisa FROM transactions "
           "WHERE status = ? AND tx_date >= ? AND tx_date <= ?")
    rows = conn.fetchall(sql, ["Completed", start, end])
    buckets = {}
    for r in rows:
        d = date.fromisoformat(r["tx_date"])
        if grain == "daily":
            key = d.isoformat()
        elif grain == "weekly":
            key = (d - timedelta(days=d.weekday())).isoformat()
        elif grain == "yearly":
            key = str(d.year)
        else:
            key = f"{d.year}-{d.month:02d}"
        b = buckets.setdefault(key, {"received": 0, "paid": 0})
        if r["type"] in CASH_IN:
            b["received"] += int(r["amount_paisa"])
        elif r["type"] in CASH_OUT:
            b["paid"] += int(r["amount_paisa"])
        elif r["type"] == "transfer" and int(r.get("fee_paisa") or 0):
            b["paid"] += int(r["fee_paisa"])
    return [{"label": k, "received": v["received"], "paid": v["paid"]}
            for k, v in sorted(buckets.items())]


def next_code(conn):
    row = conn.fetchone("SELECT MAX(id) AS m FROM transactions")
    n = (int(row["m"]) if row and row["m"] else 0) + 1
    return f"CF-{n:04d}"


def advance_due(iso, frequency):
    d = date.fromisoformat(iso)
    if frequency == "weekly":
        return (d + timedelta(days=7)).isoformat()
    if frequency == "quarterly":
        m = d.month + 3
        y = d.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        return date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()
    if frequency == "yearly":
        try:
            return date(d.year + 1, d.month, d.day).isoformat()
        except ValueError:
            return date(d.year + 1, d.month, 28).isoformat()
    m = d.month + 1
    y = d.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


def notifications(conn):
    """Due-soon / overdue / recurring-due items, minus dismissed keys."""
    seen = {r["nkey"] for r in conn.fetchall("SELECT nkey FROM notifications_seen")}
    today = today_iso()
    soon = (date.today() + timedelta(days=7)).isoformat()
    items = []
    for table, label in (("receivables", "Money to receive"), ("payables", "Money to pay")):
        for r in enrich_outstanding(conn, table,
                                     conn.fetchall(f"SELECT * FROM {table} WHERE cancelled = 0")):
            if r["outstanding_paisa"] <= 0:
                continue
            due = r.get("due_date") or ""
            if not due:
                continue
            person = conn.fetchone("SELECT name FROM people WHERE id = ?", (r.get("person_id"),))
            who = person["name"] if person else "Unknown"
            if due < today:
                items.append({"key": f"{table}-{r['id']}-overdue", "kind": "Overdue",
                              "text": f"{label} overdue: {who} — {fmt_money(r['outstanding_paisa'])} (due {fmt_date(due)})"})
            elif due <= soon:
                items.append({"key": f"{table}-{r['id']}-due", "kind": "Due soon",
                              "text": f"{label} due soon: {who} — {fmt_money(r['outstanding_paisa'])} (due {fmt_date(due)})"})
    for r in conn.fetchall("SELECT * FROM recurring WHERE active = 1"):
        if r.get("next_due") and r["next_due"] <= soon:
            items.append({"key": f"rec-{r['id']}-{r['next_due']}", "kind": "Recurring due",
                          "text": f"Recurring '{r['name']}' due {fmt_date(r['next_due'])} — {fmt_money(r['amount_paisa'])}"})
    return [i for i in items if i["key"] not in seen]

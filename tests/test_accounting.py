"""Accounting + app tests. stdlib + Flask test client only, no network."""
import os
import sys
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="crystal-test-")
os.environ["CRYSTAL_DB"] = os.path.join(_tmp, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crystal import finance as F  # noqa: E402
from crystal.db import get_conn, init_db  # noqa: E402

NOW = "2026-10-20T10:00:00"


def add_tx(conn, code, tx_date, ttype, paisa, src=None, dst=None, status="Completed",
           fee=0, person=None, category=None):
    return conn.insert(
        "INSERT INTO transactions (code, tx_date, type, amount_paisa, source_account_id, dest_account_id, status, fee_paisa, person_id, category_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (code, tx_date, ttype, paisa, src, dst, status, fee, person, category, NOW, NOW))


class AccountingTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["CRYSTAL_DB"]):
            os.remove(os.environ["CRYSTAL_DB"])
        init_db()
        self.conn = get_conn()
        self.a1 = self.conn.insert(
            "INSERT INTO accounts (name, type, opening_paisa, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("Cash A", "Cash", 10000000, NOW, NOW))  # 100,000.00
        self.a2 = self.conn.insert(
            "INSERT INTO accounts (name, type, opening_paisa, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("Bank B", "Bank", 5000000, NOW, NOW))  # 50,000.00
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_to_paisa_integer(self):
        self.assertEqual(F.to_paisa("125,000.00"), 12500000)
        self.assertEqual(F.to_paisa("100000"), 10000000)
        self.assertEqual(F.to_paisa("0.05"), 5)
        with self.assertRaises(ValueError):
            F.to_paisa("abc")

    def test_opening_balance_only(self):
        bal = F.account_balances(self.conn)
        self.assertEqual(bal[self.a1], 10000000)
        self.assertEqual(sum(bal.values()), 15000000)
        t = F.totals(self.conn)
        self.assertEqual(t["received"], 0)
        self.assertEqual(t["paid"], 0)

    def test_received_paid_totals(self):
        add_tx(self.conn, "CF-T1", "2026-10-05", "received", 5000000, src=self.a1)
        add_tx(self.conn, "CF-T2", "2026-10-06", "paid", 2000000, src=self.a1)
        t = F.totals(self.conn)
        self.assertEqual(t["received"], 5000000)
        self.assertEqual(t["paid"], 2000000)
        self.assertEqual(t["net"], 3000000)
        bal = F.account_balances(self.conn)
        self.assertEqual(bal[self.a1], 13000000)  # 100k + 50k - 20k
        self.assertEqual(sum(bal.values()), 18000000)

    def test_transfer_neutral(self):
        add_tx(self.conn, "CF-T1", "2026-10-05", "received", 5000000, src=self.a1)
        add_tx(self.conn, "CF-T2", "2026-10-06", "paid", 2000000, src=self.a1)
        add_tx(self.conn, "CF-T3", "2026-10-07", "transfer", 1000000, src=self.a1, dst=self.a2)
        t = F.totals(self.conn)
        self.assertEqual(t["received"], 5000000)  # transfer NOT income
        self.assertEqual(t["paid"], 2000000)      # transfer NOT expense
        bal = F.account_balances(self.conn)
        self.assertEqual(bal[self.a1], 12000000)
        self.assertEqual(bal[self.a2], 6000000)
        self.assertEqual(sum(bal.values()), 18000000)  # total unchanged

    def test_pending_cancelled_voided_excluded(self):
        add_tx(self.conn, "CF-T1", "2026-10-05", "received", 5000000, src=self.a1, status="Completed")
        add_tx(self.conn, "CF-T2", "2026-10-05", "received", 9990000, src=self.a1, status="Pending")
        add_tx(self.conn, "CF-T3", "2026-10-05", "received", 8880000, src=self.a1, status="Cancelled")
        add_tx(self.conn, "CF-T4", "2026-10-05", "paid", 7770000, src=self.a1, status="Voided")
        t = F.totals(self.conn)
        self.assertEqual(t["received"], 5000000)
        self.assertEqual(t["paid"], 0)

    def test_partial_receivable(self):
        per = self.conn.insert(
            "INSERT INTO people (name, created_at, updated_at) VALUES (?, ?, ?)", ("Client X", NOW, NOW))
        rid = self.conn.insert(
            "INSERT INTO receivables (person_id, original_paisa, due_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (per, 10000000, "2026-11-01", NOW, NOW))  # owes 100,000
        # planned receivable must NOT change cash
        self.assertEqual(sum(F.account_balances(self.conn).values()), 15000000)
        tid = add_tx(self.conn, "CF-T1", "2026-10-10", "received", 4000000, src=self.a1, person=per)
        self.conn.execute(
            "INSERT INTO allocations (kind, target_id, transaction_id, amount_paisa, created_at) VALUES (?, ?, ?, ?, ?)",
            ("receivable", rid, tid, 4000000, NOW))
        out = F.outstanding(self.conn, "receivables", rid)
        self.assertEqual(out, 6000000)  # 100,000 - 40,000 = 60,000
        to = F.totals_outstanding(self.conn)
        self.assertEqual(to["to_receive"], 6000000)

    def test_refund_reversal(self):
        add_tx(self.conn, "CF-T1", "2026-10-05", "paid", 1000000, src=self.a1)
        add_tx(self.conn, "CF-T2", "2026-10-06", "refund_received", 1000000, src=self.a1)
        t = F.totals(self.conn)
        self.assertEqual(t["paid"], 1000000)
        self.assertEqual(t["received"], 1000000)  # refund reverses the outflow
        self.assertEqual(t["net"], 0)
        bal = F.account_balances(self.conn)
        self.assertEqual(sum(bal.values()), 15000000)  # back to opening

    def test_monthly_report_reconciliation(self):
        add_tx(self.conn, "CF-T1", "2026-09-15", "received", 1000000, src=self.a1)   # prior month
        add_tx(self.conn, "CF-T2", "2026-10-05", "received", 5000000, src=self.a1)
        add_tx(self.conn, "CF-T3", "2026-10-20", "paid", 2000000, src=self.a1)
        rep = F.monthly_report(self.conn, 2026, 10)
        self.assertEqual(rep["opening"], 16000000)   # 150k + 10k Sept
        self.assertEqual(rep["received"], 5000000)
        self.assertEqual(rep["paid"], 2000000)
        self.assertEqual(rep["net"], 3000000)
        self.assertEqual(rep["closing"], rep["opening"] + rep["net"])
        self.assertEqual(rep["closing"], 19000000)

    def test_payable_planned_no_cash_impact(self):
        self.conn.insert(
            "INSERT INTO payables (original_paisa, due_date, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (3000000, "2026-11-01", "Developer fee", NOW, NOW))
        self.assertEqual(sum(F.account_balances(self.conn).values()), 15000000)
        to = F.totals_outstanding(self.conn)
        self.assertEqual(to["to_pay"], 3000000)

    def test_breakdown_uses_completed_only(self):
        per = self.conn.insert(
            "INSERT INTO people (name, created_at, updated_at) VALUES (?, ?, ?)", ("Client Y", NOW, NOW))
        add_tx(self.conn, "CF-T1", "2026-10-05", "received", 5000000, src=self.a1, person=per)
        add_tx(self.conn, "CF-T2", "2026-10-06", "received", 9000000, src=self.a1, person=per, status="Pending")
        rows = F.breakdown(self.conn, "2026-10-01", "2026-10-31", "person")
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["total"]), 5000000)


class AppTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["CRYSTAL_DB"]):
            os.remove(os.environ["CRYSTAL_DB"])
        from crystal.db import init_db as _init
        _init()
        import app as appmod
        appmod.app.config["TESTING"] = True
        self.client = appmod.app.test_client()

    def test_login_page(self):
        r = self.client.get("/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Crystal Finance", r.get_data(as_text=True))

    def test_protected_redirect(self):
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_index_redirects_to_dashboard(self):
        # Regression: index() once used url_for("main.dashboard") which does
        # not exist (endpoint is main.dashboard_page) -> 500 after login.
        self.client.post("/login", data={"username": "admin", "password": "Crystal@123"})
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/dashboard"), r.headers["Location"])

    def test_login_and_pages(self):
        r = self.client.post("/login", data={"username": "admin", "password": "Crystal@123"},
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Dashboard", r.get_data(as_text=True))
        pages = ["/dashboard", "/transactions", "/transactions/received", "/transactions/paid",
                 "/transactions/transfers", "/accounts", "/people", "/projects", "/receive",
                 "/pay", "/schedule", "/recurring", "/reports/monthly", "/reports/annual",
                 "/analytics", "/categories", "/attachments", "/export", "/notifications",
                 "/settings", "/trash", "/search?q=test"]
        for p in pages:
            rr = self.client.get(p)
            self.assertEqual(rr.status_code, 200, f"page failed: {p}")

    def test_create_flow_updates_dashboard(self):
        c = self.client
        c.post("/login", data={"username": "admin", "password": "Crystal@123"})
        r = c.post("/accounts", data={"name": "Test Wallet", "type": "Cash",
                                      "opening": "100000", "opening_date": "2026-10-01"},
                   follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        # find account id
        conn = get_conn()
        acc = conn.fetchone("SELECT id FROM accounts WHERE name = ?", ("Test Wallet",))
        conn.close()
        self.assertIsNotNone(acc)
        c.post("/transactions/new", data={"type": "received", "amount": "25000",
                                          "tx_date": "2026-10-21", "status": "Completed",
                                          "source_account_id": str(acc["id"]),
                                          "description": "Test income"},
               follow_redirects=True)
        c.post("/transactions/new", data={"type": "paid", "amount": "5000",
                                          "tx_date": "2026-10-22", "status": "Completed",
                                          "source_account_id": str(acc["id"]),
                                          "description": "Test expense"},
               follow_redirects=True)
        r = c.get("/dashboard?year=2026&month=10")
        html = r.get_data(as_text=True)
        self.assertIn("25,000.00", html)
        self.assertIn("5,000.00", html)
        self.assertIn("20,000.00", html)  # net
        self.assertIn("120,000.00", html)  # available balance


if __name__ == "__main__":
    unittest.main()

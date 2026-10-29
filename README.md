# Crystal Finance

Private personal finance & payment tracking web app. Flask + Jinja, SQLite
locally, Postgres (Neon via Vercel `DATABASE_URL`) in production.

## Run locally

```bash
python3 -m venv /tmp/crystal-venv
/tmp/crystal-venv/bin/pip install -r requirements.txt
/tmp/crystal-venv/bin/python app.py   # serves on http://127.0.0.1:5091
```

Tests: `/tmp/crystal-venv/bin/python -m pytest tests/` (stdlib + Flask test client, no network).

## Login

First run seeds one owner account:

- username: `admin`
- password: `Crystal@123`

A warning banner stays visible until you change the password in Settings.

## Money rules (enforced in crystal/finance.py)

- All money is INTEGER paisa (PKR x 100). No floats.
- Only Completed transactions move cash. Pending/Cancelled/Voided are excluded everywhere.
- Transfers move money between accounts, never income/expense, never change total funds.
- Receivables/payables never change cash until a Completed payment is allocated to them (partial payments reduce outstanding by exactly the allocated amount).
- Contract values are never income until cash is received.
- Reports reconcile: closing = opening + net of completed transactions in period.

## Deploy

GitHub repo `uzairareeb/crystal-finance`, Vercel project with Framework Preset **Flask**,
Neon Postgres via the Vercel Storage -> Neon integration (`DATABASE_URL` is auto-set).
Without `DATABASE_URL` the app uses SQLite (`/tmp/crystal.db` on Vercel).

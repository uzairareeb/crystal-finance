"""Crystal Finance — entry point only. All logic lives in crystal/ modules."""
import os

from flask import Flask

from crystal.auth import ensure_owner
from crystal.db import init_db

app = Flask(__name__)
app.secret_key = os.environ.get("CRYSTAL_SECRET", "crystal-finance-dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

init_db()
ensure_owner()

from crystal import routes_accounts, routes_auth, routes_data, routes_main, routes_people, routes_reports, routes_tx

app.register_blueprint(routes_auth.bp)
app.register_blueprint(routes_main.bp)
app.register_blueprint(routes_tx.bp)
app.register_blueprint(routes_people.bp)
app.register_blueprint(routes_accounts.bp)
app.register_blueprint(routes_reports.bp)
app.register_blueprint(routes_data.bp)


@app.context_processor
def inject_globals():
    from flask import session
    return {"username": session.get("username", "")}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5091))
    app.run(host="127.0.0.1", port=port, debug=False)

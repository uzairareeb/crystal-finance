from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from crystal.auth import ensure_owner, login_required, verify

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    ensure_owner()
    if request.method == "POST":
        user = verify(request.form.get("username", ""), request.form.get("password", ""))
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(request.args.get("next") or url_for("main.dashboard_page"))
        flash("Invalid username or password.", "err")
    return render_template("login.html", title="Sign in")


@bp.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

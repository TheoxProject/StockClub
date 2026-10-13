from datetime import datetime, timedelta, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from . import bp
from ..extensions import db
from ..models import User

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=10)


def _utc_datetime(value):
    """SQLite may return a naive datetime for timezone-aware columns."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("inventory.index"))
    if request.method == "POST":
        login_name = request.form.get("login", "").strip().casefold()
        password = request.form.get("password", "")
        user = db.session.query(User).filter_by(login=login_name).first()
        now = datetime.now(timezone.utc)

        if user and user.is_active:
            locked_until = _utc_datetime(user.locked_until)
            if locked_until and locked_until > now:
                flash("Login ou mot de passe incorrect. Réessaie dans quelques minutes.", "error")
                return render_template("auth/login.html"), 200

            if len(password) >= 6 and user.check_password(password):
                user.failed_login_attempts = 0
                user.locked_until = None
                db.session.commit()
                login_user(user, remember=request.form.get("remember") == "on", fresh=True)
                return redirect(url_for("inventory.index"))

            user.failed_login_attempts += 1
            if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                user.failed_login_attempts = MAX_FAILED_ATTEMPTS
                user.locked_until = now + LOCKOUT_DURATION
            db.session.commit()

        flash("Login ou mot de passe incorrect.", "error")
    return render_template("auth/login.html")


@bp.post("/logout")
def logout():
    logout_user()
    flash("Tu es déconnecté.", "info")
    return redirect(url_for("auth.login"))

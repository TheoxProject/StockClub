from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from flask import abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from . import bp
from ..extensions import db
from ..models import Invitation, User

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=10)


def _utc_datetime(value):
    """SQLite may return a naive datetime for timezone-aware columns."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _valid_invitation(token: str, *, lock: bool = False):
    if not token or len(token) > 200:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    query = Invitation.query.filter_by(token_hash=token_hash)
    if lock:
        query = query.with_for_update()
    invitation = query.first()
    if invitation is None or invitation.used_at or invitation.revoked_at:
        return None
    if _utc_datetime(invitation.expires_at) <= datetime.now(timezone.utc):
        return None
    return invitation


def _register_error(message: str, token: str):
    flash(message, "error")
    response = make_response(render_template("auth/register.html", invitation_token=token), 200)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


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


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Shared-code registration is disabled; users must use an admin-issued one-use link."""
    return redirect(url_for("auth.login"))


@bp.route("/register/<token>", methods=["GET", "POST"])
def register_invitation(token):
    """Redeem a single-use invitation. New accounts are always regular members."""
    if current_user.is_authenticated:
        return redirect(url_for("inventory.index"))

    invitation = _valid_invitation(token)
    if invitation is None:
        return render_template(
            "error.html", code=410,
            message="Ce lien d’invitation est invalide, expiré ou a déjà été utilisé. Demande un nouveau lien à un administrateur.",
        ), 410

    if request.method == "GET":
        response = make_response(render_template("auth/register.html", invitation_token=token), 200)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Re-fetch with a row lock so two simultaneous submissions cannot redeem the same link.
    invitation = _valid_invitation(token, lock=True)
    if invitation is None:
        return render_template(
            "error.html", code=410,
            message="Ce lien d’invitation est invalide, expiré ou a déjà été utilisé. Demande un nouveau lien à un administrateur.",
        ), 410

    name = request.form.get("name", "").strip()
    login_name = request.form.get("login", "").strip().casefold()
    password = request.form.get("password", "")
    password_confirmation = request.form.get("password_confirmation", "")

    if not name or len(name) > 120:
        return _register_error("Indique un nom ou prénom (120 caractères maximum).", token)

    if (
        not login_name
        or len(login_name) > 80
        or any(ch.isspace() for ch in login_name)
        or "@" in login_name
    ):
        return _register_error("Le login doit contenir de 1 à 80 caractères, sans espace ni adresse e-mail.", token)

    if len(password) < 6:
        return _register_error("Le mot de passe doit comporter au moins 6 caractères.", token)
    if len(password) > 128:
        return _register_error("Le mot de passe ne peut pas dépasser 128 caractères.", token)
    if password != password_confirmation:
        return _register_error("Les deux mots de passe ne correspondent pas.", token)

    if User.query.filter_by(login=login_name).first():
        return _register_error("Ce nom de login est déjà utilisé. Choisis-en un autre.", token)

    user = User(name=name, login=login_name, role="member")
    user.set_password(password)
    invitation.used_at = datetime.now(timezone.utc)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        # Unique login constraints and invitation redemption are protected transactionally.
        db.session.rollback()
        return _register_error("Ce nom de login est déjà utilisé ou ce lien vient d’être utilisé. Vérifie les informations ou demande un nouveau lien.", token)

    flash("Compte créé. Tu peux maintenant te connecter.", "success")
    return redirect(url_for("auth.login"))


@bp.post("/logout")
def logout():
    logout_user()
    flash("Tu es déconnecté.", "info")
    return redirect(url_for("auth.login"))

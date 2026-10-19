from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from flask import abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import bp
from ..extensions import csrf, db
from ..models import BootstrapLock, Invitation, User

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=10)

# A signed, short-lived form proof for public one-use invitations. Unlike the
# global Flask-WTF token, it does not require a browser session cookie: some
# mail app in-app browsers discard that cookie between displaying and posting.
INVITATION_FORM_MAX_AGE = 2 * 60 * 60


def _invitation_form_signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="stock-club-invitation-form-v1")


def _invitation_form_proof(invitation_token: str) -> str:
    digest = hashlib.sha256(invitation_token.encode("utf-8")).hexdigest()
    return _invitation_form_signer().dumps(digest)


def _valid_invitation_form_proof(invitation_token: str, proof: str) -> bool:
    if not proof or len(proof) > 1024:
        return False
    try:
        actual = _invitation_form_signer().loads(proof, max_age=INVITATION_FORM_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    expected = hashlib.sha256(invitation_token.encode("utf-8")).hexdigest()
    return isinstance(actual, str) and hmac.compare_digest(actual, expected)


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
    response = make_response(render_template("auth/register.html", invitation_token=token, invitation_form_proof=_invitation_form_proof(token)), 200)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _needs_first_admin() -> bool:
    """True only while no account exists; no invitation or public signup needed."""
    return User.query.limit(1).first() is None


def _render_login(status=200):
    return render_template("auth/login.html", needs_first_admin=_needs_first_admin()), status


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
                return _render_login(200)

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
    return _render_login()


@bp.route("/first-admin", methods=["GET", "POST"])
def first_admin():
    """Initialize the instance once, when the users table is completely empty."""
    if current_user.is_authenticated:
        return redirect(url_for("inventory.index"))

    form_data = {
        "name": request.form.get("name", ""),
        "login": request.form.get("login", ""),
    }
    if request.method == "GET":
        if not _needs_first_admin():
            flash("Le premier compte a déjà été créé. Connecte-toi avec ton compte.", "info")
            return redirect(url_for("auth.login"))
        return render_template("auth/first_admin.html", form=form_data)

    # Start the critical section with the singleton lock update, after dropping
    # any read transaction Flask-Login may have opened. The locked transaction
    # then checks whether any user exists and holds the lock through the commit.
    db.session.rollback()
    try:
        lock_result = db.session.execute(
            update(BootstrapLock)
            .where(BootstrapLock.id == 1)
            .values(lock_version=BootstrapLock.lock_version + 1)
        )
        if lock_result.rowcount != 1:
            db.session.rollback()
            current_app.logger.error("Bootstrap lock row is missing; run database migrations.")
            flash("L’initialisation n’est pas prête. Recharge la page ou contacte l’administrateur technique.", "error")
            return redirect(url_for("auth.login"))

        if User.query.limit(1).first() is not None:
            db.session.rollback()
            flash("Le premier compte vient d’être créé. Connecte-toi avec ce compte.", "info")
            return redirect(url_for("auth.login"))

        name = form_data["name"].strip()
        login_name = form_data["login"].strip().casefold()
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")

        def form_error(message):
            db.session.rollback()  # Release the singleton lock on invalid input.
            flash(message, "error")
            return render_template("auth/first_admin.html", form=form_data), 400

        if not name or len(name) > 120:
            return form_error("Indique un nom affiché (120 caractères maximum).")
        if (
            not login_name
            or len(login_name) > 80
            or any(ch.isspace() for ch in login_name)
            or "@" in login_name
        ):
            return form_error("Le login doit contenir de 1 à 80 caractères, sans espace ni adresse e-mail.")
        if len(password) < 6:
            return form_error("Le mot de passe doit comporter au moins 6 caractères.")
        if len(password) > 128:
            return form_error("Le mot de passe ne peut pas dépasser 128 caractères.")
        if password != password_confirmation:
            return form_error("Les deux mots de passe ne correspondent pas.")

        user = User(name=name, login=login_name, role="admin")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
    except (IntegrityError, OperationalError):
        db.session.rollback()
        # A competing bootstrap request can win the unique/locking race. This
        # error is handled without ever creating a second account.
        if not _needs_first_admin():
            flash("Le premier compte vient d’être créé. Connecte-toi avec ce compte.", "info")
            return redirect(url_for("auth.login"))
        flash("L’initialisation a été interrompue. Recharge la page puis réessaie.", "error")
        return render_template("auth/first_admin.html", form=form_data), 409

    login_user(user, fresh=True)
    flash("Premier compte administrateur créé. Bienvenue dans Stock Club !", "success")
    return redirect(url_for("inventory.index"))

@bp.route("/register", methods=["GET", "POST"])
def register():
    """Shared-code registration is disabled; users must use an admin-issued one-use link."""
    return redirect(url_for("auth.login"))


@bp.route("/register/<token>", methods=["GET", "POST"])
@csrf.exempt  # This endpoint validates its own signed, invitation-bound form proof below.
def register_invitation(token):
    """Redeem a single-use invitation. New accounts are always regular members."""
    if current_user.is_authenticated:
        return redirect(url_for("inventory.index"))
    # A one-use invitation must never bypass first-run administrator setup.
    if _needs_first_admin():
        return redirect(url_for("auth.first_admin"))

    invitation = _valid_invitation(token)
    if invitation is None:
        return render_template(
            "error.html", code=410,
            message="Ce lien d’invitation est invalide, expiré ou a déjà été utilisé. Demande un nouveau lien à un administrateur.",
        ), 410

    if request.method == "GET":
        response = make_response(render_template("auth/register.html", invitation_token=token, invitation_form_proof=_invitation_form_proof(token)), 200)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Validate before processing registration. The invitation URL alone is not
    # accepted as a form submission: the GET response supplies a signed proof.
    if not _valid_invitation_form_proof(token, request.form.get("invitation_form_proof", "")):
        return render_template(
            "error.html", code=400,
            message="Le formulaire a expiré ou n’est plus valide. Rouvre le lien d’invitation et réessaie.",
        ), 400

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

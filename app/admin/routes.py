from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from flask import abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from . import bp
from .decorators import admin_required
from ..extensions import db
from ..models import Invitation, User


def _now_utc():
    return datetime.now(timezone.utc)


def _active_admin_count() -> int:
    return User.query.filter_by(role="admin", is_active_user=True).count()


def _invitation_status(invitation, now):
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if invitation.used_at is not None:
        return "Utilisé"
    if invitation.revoked_at is not None:
        return "Révoqué"
    if expires_at <= now:
        return "Expiré"
    return "En attente"


@bp.get("/members")
@admin_required
def members():
    now = _now_utc()
    users = User.query.order_by(User.name.asc(), User.id.asc()).all()
    invitations = Invitation.query.order_by(Invitation.created_at.desc()).limit(50).all()
    invitation_rows = [(invitation, _invitation_status(invitation, now)) for invitation in invitations]
    return render_template("admin/members.html", users=users, invitation_rows=invitation_rows, now=now)


@bp.post("/invitations")
@admin_required
def create_invitation():
    # Persist only a SHA-256 hash. The raw URL is displayed once to the issuing admin.
    token = secrets.token_urlsafe(32)
    expires_at = _now_utc() + timedelta(days=7)
    invitation = Invitation(
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        created_by_id=current_user.id,
        expires_at=expires_at,
    )
    db.session.add(invitation)
    db.session.commit()
    invitation_url = url_for("auth.register_invitation", token=token, _external=True)
    response = make_response(render_template("admin/invitation_created.html", invitation_url=invitation_url, expires_at=expires_at), 200)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.post("/invitations/<int:invitation_id>/cancel")
@admin_required
def cancel_invitation(invitation_id):
    invitation = db.get_or_404(Invitation, invitation_id)
    if invitation.used_at is None and invitation.revoked_at is None:
        invitation.revoked_at = _now_utc()
        db.session.commit()
        flash("Le lien d’invitation a été révoqué.", "success")
    else:
        flash("Ce lien a déjà été utilisé ou révoqué.", "info")
    return redirect(url_for("admin.members"))


@bp.post("/members/<int:user_id>/role")
@admin_required
def change_role(user_id):
    user = db.get_or_404(User, user_id)
    new_role = request.form.get("role", "").strip()
    if new_role not in {"admin", "member"}:
        abort(400)

    if user.id == current_user.id and new_role != "admin":
        flash("Tu ne peux pas retirer toi-même tes droits d’administrateur.", "error")
        return redirect(url_for("admin.members"))

    if user.is_active_user and user.role == "admin" and new_role != "admin" and _active_admin_count() <= 1:
        flash("Impossible de retirer le dernier administrateur actif.", "error")
        return redirect(url_for("admin.members"))

    user.role = new_role
    db.session.commit()
    flash(f"Rôle mis à jour pour {user.name} : {user.role_label}.", "success")
    return redirect(url_for("admin.members"))


@bp.post("/members/<int:user_id>/toggle-active")
@admin_required
def toggle_member_active(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("Tu ne peux pas désactiver ton propre compte.", "error")
        return redirect(url_for("admin.members"))

    if user.is_active_user and user.role == "admin" and _active_admin_count() <= 1:
        flash("Impossible de désactiver le dernier administrateur actif.", "error")
        return redirect(url_for("admin.members"))

    user.is_active_user = not user.is_active_user
    db.session.commit()
    state = "activé" if user.is_active_user else "désactivé"
    flash(f"Compte de {user.name} {state}.", "success")
    return redirect(url_for("admin.members"))

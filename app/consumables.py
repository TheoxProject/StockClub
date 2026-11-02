"""Atomic consumptions and replenishments with durable submission receipts."""
import hashlib
import json
import uuid
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .box_operations import available, content, get_box, sources_for
from .extensions import db
from .models import Borrowing, BorrowingEvent, ConsumableReceipt, Consumption, Item, StockMovement, StorageBox, utcnow
from .transactions import synchronized

bp = Blueprint("consumables", __name__)
MAXIMUM = 1000000


def issue_token(operation, target):
    return URLSafeTimedSerializer(current_app.secret_key, salt="consumables-v1").dumps(
        {"id": str(uuid.uuid4()), "user": current_user.id, "operation": operation, "target": target})


def receipt(operation, target):
    try:
        data = URLSafeTimedSerializer(current_app.secret_key, salt="consumables-v1").loads(request.form.get("operation_token", ""), max_age=86400)
        if not isinstance(data, dict) or type(data.get("id")) is not str or type(data.get("user")) is not int or type(data.get("target")) is not int or type(data.get("operation")) is not str:
            raise ValueError
        ident = str(uuid.UUID(data["id"]))
        if data != {"id": ident, "user": current_user.id, "operation": operation, "target": target}:
            raise ValueError
    except (BadSignature, ValueError, TypeError, KeyError):
        abort(400, description="Cette demande a expiré ou est invalide. Recharge la page.")
    payload = {key: request.form.getlist(key) for key in sorted(request.form) if key not in ("csrf_token", "operation_token")}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    existing = db.session.get(ConsumableReceipt, ident)
    if existing:
        if existing.user_id != current_user.id or existing.operation != operation or existing.target_id != target or existing.payload_hash != digest:
            abort(409, description="Cette demande a déjà été utilisée avec d'autres données.")
        return True
    db.session.add(ConsumableReceipt(id=ident, user_id=current_user.id, operation=operation, target_id=target, payload_hash=digest))
    return False


def fields(require_quantity=True, require_note=False):
    note = request.form.get("note", "").strip()
    if len(note) > 240 or (require_note and not note):
        raise ValueError("Le commentaire doit contenir au plus 240 caractères ; un motif est obligatoire pour une annulation.")
    if not require_quantity:
        return None, note
    raw = request.form.get("quantity", "")
    if not raw or not raw.isascii() or not raw.isdecimal() or len(raw) > 7 or not 1 <= int(raw) <= MAXIMUM:
        raise ValueError("La quantité doit être un entier entre 1 et 1 000 000.")
    return int(raw), note


def record(item, kind, amount, note, *, loan=None, box=None, box_snapshot="", original=None, restored_outside=False):
    entry = Consumption(kind=kind, item=item, item_name_snapshot=item.name, borrowing=loan, box=box,
                        box_name_snapshot=box_snapshot or (box.name if box else ""), user=current_user,
                        quantity=amount, note=note, original=original, restored_outside=restored_outside)
    db.session.add(entry)
    return entry


def movement(item, kind, amount, delta, note, loan=None, box=None, box_snapshot="", reason=""):
    db.session.add(StockMovement(item=item, item_name_snapshot=item.name, user=current_user,
        movement_type=kind, delta=delta, total_delta=amount, quantity_after=item.quantity, reason=reason,
        note=note, borrowing_id=loan.id if loan else None, box=box, box_name_snapshot=box_snapshot or (box.name if box else "")))


def rollback_on_error(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except Exception:
            db.session.rollback()
            raise
    return wrapped


def fail(exc):
    db.session.rollback()
    flash(str(exc), "error")
    return redirect(request.path)


def form(operation, target, item, *, loan=None, entry=None):
    sources = sources_for(item)[0] if operation == "direct" else []
    return render_template("consumables/form.html", operation=operation, item=item, loan=loan, entry=entry,
                           sources=sources, operation_token=issue_token(operation, target))


@bp.route('/items/<int:item_id>/consume', methods=['GET', 'POST'])
@login_required
@synchronized()
@rollback_on_error
def consume_item(item_id):
    if request.method == 'POST':
        if receipt('direct', item_id):
            db.session.rollback()
            return redirect(url_for('inventory.item_detail', item_id=item_id))
        try:
            item = Item.query.filter_by(id=item_id).populate_existing().first()
            if not item or not item.is_consumable:
                raise ValueError("Cet objet n'est plus un consommable disponible.")
            amount, note = fields()
            if 'source_box' not in request.form:
                raise ValueError("Choisis une provenance.")
            box = get_box(request.form.get('source_box', ''))
            if amount > available(item, box) or amount > item.quantity:
                raise ValueError("La quantité disponible à cette provenance est insuffisante.")
            if box:
                content(item, box).present_quantity -= amount
            item.quantity -= amount
            item.updated_by = current_user
            item.updated_at = utcnow()
            record(item, 'direct', amount, note, box=box)
            movement(item, 'consommation_directe', -amount, -amount, note, box=box)
            db.session.commit()
            flash("Consommation enregistrée.", "success")
            return redirect(url_for('inventory.item_detail', item_id=item_id))
        except ValueError as exc:
            return fail(exc)
    item = db.get_or_404(Item, item_id)
    if not item.is_consumable:
        abort(404)
    return form('direct', item_id, item)


@bp.route('/loans/<int:loan_id>/consume', methods=['GET', 'POST'])
@login_required
@synchronized()
@rollback_on_error
def consume_loan(loan_id):
    if request.method == 'POST':
        if receipt('loan', loan_id):
            db.session.rollback()
            return redirect(url_for('inventory.loans'))
        try:
            loan = Borrowing.query.filter_by(id=loan_id).populate_existing().first()
            item = loan.item if loan else None
            if not item or not item.is_consumable:
                raise ValueError("Cet objet n'est plus un consommable disponible.")
            amount, note = fields()
            if amount > loan.remaining_quantity:
                raise ValueError("La quantité dépasse le restant à rendre.")
            loan.quantity_consumed += amount
            if not loan.remaining_quantity:
                loan.closed_at = utcnow()
            item.updated_by = current_user
            item.updated_at = utcnow()
            record(item, 'loan', amount, note, loan=loan, box=loan.box, box_snapshot=loan.box_name_snapshot)
            db.session.add(BorrowingEvent(borrowing=loan, item=item, item_name_snapshot=item.name, user=current_user, event_type='consommation', quantity=amount, note=note))
            movement(item, 'consommation_emprunt', -amount, 0, note, loan=loan, box=loan.box, box_snapshot=loan.box_name_snapshot)
            db.session.commit()
            flash("Consommation enregistrée.", "success")
            return redirect(url_for('inventory.loans'))
        except ValueError as exc:
            return fail(exc)
    loan = db.get_or_404(Borrowing, loan_id)
    if not loan.item or not loan.item.is_consumable or not loan.is_open:
        abort(404)
    return form('loan', loan_id, loan.item, loan=loan)


@bp.route('/items/<int:item_id>/replenish', methods=['GET', 'POST'])
@login_required
@synchronized()
@rollback_on_error
def replenish(item_id):
    if request.method == 'POST':
        if receipt('replenish', item_id):
            db.session.rollback()
            return redirect(url_for('inventory.item_detail', item_id=item_id))
        try:
            item = Item.query.filter_by(id=item_id).populate_existing().first()
            if not item or not item.is_consumable:
                raise ValueError("Cet objet n'est plus un consommable disponible.")
            amount, note = fields()
            if item.total_quantity + amount > MAXIMUM:
                raise ValueError("Le stock total dépasserait 1 000 000 unités.")
            item.quantity += amount
            item.updated_by = current_user
            item.updated_at = utcnow()
            movement(item, 'reapprovisionnement', amount, amount, note)
            db.session.commit()
            flash("Réapprovisionnement enregistré hors boîte.", "success")
            return redirect(url_for('inventory.item_detail', item_id=item_id))
        except ValueError as exc:
            return fail(exc)
    item = db.get_or_404(Item, item_id)
    if not item.is_consumable:
        abort(404)
    return form('replenish', item_id, item)


@bp.route('/consumptions/<int:consumption_id>/cancel', methods=['GET', 'POST'])
@login_required
@synchronized(admin=True)
@rollback_on_error
def cancel(consumption_id):
    if not current_user.is_admin:
        abort(403)
    if request.method == 'POST':
        if receipt('cancel', consumption_id):
            db.session.rollback()
            return redirect(url_for('inventory.index'))
        try:
            entry = Consumption.query.filter_by(id=consumption_id).populate_existing().first()
            if not entry or entry.kind not in ('direct', 'loan') or entry.cancellation:
                raise ValueError("Cette consommation est introuvable ou déjà annulée.")
            _, note = fields(False, True)
            item = entry.item
            if not item:
                raise ValueError("La fiche objet a été supprimée : correction impossible.")
            amount = entry.quantity
            if item.total_quantity + amount > MAXIMUM:
                raise ValueError("Le stock total dépasserait 1 000 000 unités.")
            loan = entry.borrowing
            box = entry.box
            restored_outside = False
            if entry.kind == 'loan':
                if not loan or loan.item_id != item.id or loan.quantity_consumed < amount:
                    raise ValueError("L'emprunt d'origine est indisponible : correction impossible.")
                if entry.box_name_snapshot and (not box or not box.is_active or loan.box_id != box.id):
                    raise ValueError("La boîte d'origine de l'emprunt est indisponible : correction impossible.")
                loan.quantity_consumed -= amount
                loan.closed_at = None
                db.session.add(BorrowingEvent(borrowing=loan, item=item, item_name_snapshot=item.name, user=current_user, event_type='annulation_conso', quantity=amount, note=note))
                delta = 0
            else:
                if entry.box_name_snapshot and (not box or not box.is_active):
                    if request.form.get('restore_outside') != '1':
                        raise ValueError("La boîte d'origine est indisponible : confirme la restitution hors boîte.")
                    box = None
                    restored_outside = True
                if box:
                    content(item, box, True).present_quantity += amount
                item.quantity += amount
                delta = amount
            item.updated_by = current_user
            item.updated_at = utcnow()
            record(item, 'cancellation', amount, note, loan=loan, box=box, box_snapshot=entry.box_name_snapshot, original=entry, restored_outside=restored_outside)
            movement(item, 'annulation_consommation', amount, delta, note, loan=loan, box=box, box_snapshot=entry.box_name_snapshot, reason='hors_boite' if restored_outside else '')
            db.session.commit()
            flash("Consommation annulée.", "success")
            return redirect(url_for('inventory.item_detail', item_id=item.id))
        except ValueError as exc:
            return fail(exc)
    entry = db.get_or_404(Consumption, consumption_id)
    if entry.kind not in ('direct', 'loan'):
        abort(404)
    if entry.cancellation:
        return render_template('consumables/cancel_status.html', entry=entry, message='Cette consommation a déjà été annulée.'), 200
    if not entry.item:
        return render_template('consumables/cancel_status.html', entry=entry, message='La fiche objet a été supprimée : correction impossible.'), 409
    return form('cancel', consumption_id, entry.item, entry=entry)



@bp.get('/consumptions')
@login_required
def history():
    entries = Consumption.query.order_by(Consumption.created_at.desc(), Consumption.id.desc()).limit(100).all()
    replenishments = StockMovement.query.filter_by(movement_type='reapprovisionnement').order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(100).all()
    rows = [(entry.created_at, entry.id, 'consumption', entry) for entry in entries]
    rows += [(entry.created_at, entry.id, 'replenishment', entry) for entry in replenishments]
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return render_template('consumables/history.html', rows=rows[:100])

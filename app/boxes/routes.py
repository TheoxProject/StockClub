from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from ..admin.decorators import admin_required
from ..box_operations import available, checkout as do_checkout, content, get_box, return_loan
from ..extensions import db
from ..models import BoxContent, BoxEvent, BoxFillReceipt, Borrowing, BorrowingEvent, Item, LoanGroup, StorageBox
from ..box_fill import category_data, descendants, issue_token, option_row, parse_selection, stock_query, submitted_hash, verify_token
from ..transactions import synchronized
from . import bp


def active_box(box_id):
    return StorageBox.query.filter_by(id=box_id, is_active=True).first_or_404()


def box_rows(box):
    contents = {c.item_id: c for c in BoxContent.query.filter_by(box_id=box.id).all()}
    loans = Borrowing.query.filter_by(box_id=box.id).filter(Borrowing.quantity_out > Borrowing.quantity_returned + Borrowing.quantity_lost + Borrowing.quantity_consumed).all()
    outstanding = {}
    for loan in loans:
        outstanding[loan.item_id] = outstanding.get(loan.item_id, 0) + loan.remaining_quantity
    ids = set(contents) | set(outstanding)
    items = Item.query.filter(Item.id.in_(ids)).order_by(Item.name).all() if ids else []
    return [dict(item=item, present=contents[item.id].present_quantity if item.id in contents else 0, outstanding=outstanding.get(item.id, 0), assigned=(contents[item.id].present_quantity if item.id in contents else 0) + outstanding.get(item.id, 0)) for item in items]


def selected_lines(valid):
    raw = request.form.getlist('selected')
    if not raw or len(raw) != len(set(raw)):
        raise ValueError("Sélectionne au moins une ligne distincte.")
    result = []
    for value in raw:
        try:
            ident = int(value)
            amount = int(request.form.get('quantity_' + value, ''))
        except (ValueError, TypeError):
            raise ValueError("Sélection ou quantité invalide.")
        if str(ident) != value or ident not in valid or amount <= 0 or amount > 1000000:
            raise ValueError("Sélection ou quantité invalide. Actualise la page.")
        result.append((valid[ident], amount))
    return sorted(result, key=lambda pair: pair[0].id)


def box_form(box=None):
    name = ' '.join(request.form.get('name', '').split())
    description = request.form.get('description', '').strip()
    key = name.casefold()
    if not name or len(name) > 160 or len(key) > 320 or len(description) > 5000:
        raise ValueError("Nom obligatoire (160 caractères maximum) ou description invalide.")
    query = StorageBox.query.filter_by(name_key=key)
    if box:
        query = query.filter(StorageBox.id != box.id)
    if query.first():
        raise ValueError("Une boîte porte déjà ce nom.")
    return name, key, description


@bp.get('/')
@login_required
def index():
    q = request.args.get('q', '').strip()[:120]
    query = StorageBox.query.filter_by(is_active=True)
    if q:
        query = query.filter(or_(StorageBox.name.ilike(f'%{q}%'), StorageBox.description.ilike(f'%{q}%')))
    return render_template('boxes/index.html', boxes=query.order_by(StorageBox.name).all(), q=q)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@admin_required
@synchronized(admin=True)
def create():
    if request.method == 'POST':
        try:
            name, key, description = box_form()
            box = StorageBox(name=name, name_key=key, description=description)
            db.session.add(box)
            db.session.commit()
            return redirect(url_for('boxes.add_material' if request.form.get('next_action') == 'add' else 'boxes.detail', box_id=box.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return render_template('boxes/form.html', box=None, form=request.form), 400
    return render_template('boxes/form.html', box=None, form=request.form)


@bp.route('/<int:box_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@synchronized(admin=True)
def edit(box_id):
    box = active_box(box_id)
    if request.method == 'POST':
        try:
            box.name, box.name_key, box.description = box_form(box)
            db.session.commit()
            return redirect(url_for('boxes.detail', box_id=box.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return render_template('boxes/form.html', box=box, form=request.form), 400
    return render_template('boxes/form.html', box=box, form=request.form)


@bp.get('/<int:box_id>')
@login_required
def detail(box_id):
    box = db.get_or_404(StorageBox, box_id)
    loans = Borrowing.query.filter_by(box_id=box.id).filter(Borrowing.quantity_out > Borrowing.quantity_returned + Borrowing.quantity_lost + Borrowing.quantity_consumed).order_by(Borrowing.created_at).all()
    events = BoxEvent.query.filter(or_(BoxEvent.source_box_id == box.id, BoxEvent.destination_box_id == box.id)).order_by(BoxEvent.created_at.desc(), BoxEvent.id.desc()).all()
    groups = LoanGroup.query.filter_by(box_id=box.id).order_by(LoanGroup.created_at.desc()).all()
    recent_loan_events = BorrowingEvent.query.join(Borrowing, BorrowingEvent.borrowing_id == Borrowing.id).filter(Borrowing.box_id == box.id).order_by(BorrowingEvent.created_at.desc(), BorrowingEvent.id.desc()).all()
    return render_template('boxes/detail.html', recent_loan_events=recent_loan_events, box=box, rows=box_rows(box), open_loans=loans, events=events, groups=groups, boxes=StorageBox.query.filter_by(is_active=True).order_by(StorageBox.name).all(), items=Item.query.order_by(Item.name).all())


@bp.post('/<int:box_id>/transfer')
@login_required
@admin_required
@synchronized(admin=True)
def transfer(box_id):
    box = active_box(box_id)
    try:
        source = get_box(request.form.get('source_box_id', '').strip())
        destination = get_box(request.form.get('destination_box_id', '').strip())
        item_id = int(request.form.get('item_id', ''))
        if item_id < 1 or item_id > 9223372036854775807:
            raise ValueError('Objet invalide.')
        item = db.session.get(Item, item_id)
        amount = int(request.form.get('quantity', ''))
        if not item or amount <= 0 or amount > 1000000 or (source.id if source else None) == (destination.id if destination else None) or box.id not in {source.id if source else None, destination.id if destination else None}:
            raise ValueError('Transfert invalide.')
        if available(item, source) < amount:
            raise ValueError('Quantité présente insuffisante. Actualise la page.')
        if source:
            content(item, source).present_quantity -= amount
        if destination:
            content(item, destination, True).present_quantity += amount
        db.session.add(BoxEvent(item_id=item.id, item_name_snapshot=item.name, quantity=amount, source_box_id=source.id if source else None, destination_box_id=destination.id if destination else None, source_name_snapshot=source.name if source else 'Hors boîte', destination_name_snapshot=destination.name if destination else 'Hors boîte', user_id=current_user.id))
        db.session.commit()
        flash('Transfert enregistré.', 'success')
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        flash(str(exc) or 'Transfert invalide.', 'error')
    except Exception:
        db.session.rollback()
        raise
    return redirect(url_for('boxes.detail', box_id=box.id))


@bp.route('/<int:box_id>/checkout', methods=['GET', 'POST'])
@login_required
@synchronized()
def checkout(box_id):
    box = active_box(box_id)
    rows = box_rows(box)
    if request.method == 'POST':
        try:
            lines = selected_lines({row['item'].id: row['item'] for row in rows})
            for item, amount in lines:
                if amount > available(item, box) or amount > item.quantity:
                    raise ValueError('Stock modifié. Actualise la page.')
            group = LoanGroup(box=box, box_name_snapshot=box.name, user=current_user)
            db.session.add(group)
            for item, amount in lines:
                do_checkout(item, box, amount, current_user, group=group, maximum=1000000)
            db.session.commit()
            flash('Sortie groupée enregistrée.', 'success')
            return redirect(url_for('boxes.detail', box_id=box.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return render_template('boxes/checkout.html', box=box, rows=box_rows(box), form=request.form), 400
        except Exception:
            db.session.rollback()
            raise
    return render_template('boxes/checkout.html', box=box, rows=rows, form=request.form)


@bp.route('/<int:box_id>/return', methods=['GET', 'POST'])
@login_required
@synchronized()
def return_box(box_id):
    box = active_box(box_id)
    loans = Borrowing.query.filter_by(box_id=box.id).filter(Borrowing.quantity_out > Borrowing.quantity_returned + Borrowing.quantity_lost + Borrowing.quantity_consumed).order_by(Borrowing.created_at, Borrowing.id).all()
    if request.method == 'POST':
        try:
            lines = selected_lines({loan.id: loan for loan in loans})
            totals = {}
            for loan, amount in lines:
                if amount > loan.remaining_quantity or not loan.item:
                    raise ValueError('Emprunt modifié. Actualise la page.')
                totals[loan.item_id] = totals.get(loan.item_id, 0) + amount
            if any(db.session.get(Item, ident).quantity + amount > 1000000 for ident, amount in totals.items()):
                raise ValueError('Le retour dépasse la limite de stock.')
            for loan, amount in lines:
                return_loan(loan, amount, current_user)
            db.session.commit()
            flash('Retour groupé enregistré.', 'success')
            return redirect(url_for('boxes.detail', box_id=box.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return render_template('boxes/return.html', box=box, open_loans=loans, form=request.form), 400
        except Exception:
            db.session.rollback()
            raise
    return render_template('boxes/return.html', box=box, open_loans=loans, form=request.form)


@bp.post('/<int:box_id>/delete')
@login_required
@admin_required
@synchronized(admin=True)
def delete(box_id):
    box = active_box(box_id)
    if Borrowing.query.filter_by(box_id=box.id).filter(Borrowing.quantity_out > Borrowing.quantity_returned + Borrowing.quantity_lost + Borrowing.quantity_consumed).first():
        flash('Impossible de supprimer cette boîte : des emprunts restent ouverts.', 'error')
        return redirect(url_for('boxes.detail', box_id=box.id))
    try:
        for row in BoxContent.query.filter_by(box_id=box.id).all():
            if row.present_quantity:
                db.session.add(BoxEvent(item_id=row.item_id, item_name_snapshot=row.item.name, quantity=row.present_quantity, source_box_id=box.id, destination_box_id=None, source_name_snapshot=box.name, destination_name_snapshot='Hors boîte', user_id=current_user.id))
            db.session.delete(row)
        box.is_active = False
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    flash('Boîte supprimée ; son contenu est désormais hors boîte.', 'success')
    return redirect(url_for('boxes.index'))


@bp.get('/<int:box_id>/material-options')
@login_required
@admin_required
def material_options(box_id):
    active_box(box_id)
    _, paths, children = category_data()
    query = stock_query(box_id, available_only=True)
    q = request.args.get('q', '').strip()[:120]
    if q:
        query = query.filter(Item.name.ilike(f'%{q}%'))
    category = request.args.get('category', '').strip()
    if category:
        try:
            category_id = int(category)
            if not 0 < category_id <= 9223372036854775807:
                category_id = -1
        except ValueError:
            category_id = -1
        query = query.filter(Item.category_id.in_(descendants(category_id, children)))
    try:
        page = min(1000000, max(1, int(request.args.get('page', '1'))))
    except ValueError:
        page = 1
    pagination = query.order_by(Item.name, Item.id).paginate(page=page, per_page=24, error_out=False)
    return jsonify(rows=[option_row(item, available, present, paths) for item, available, present in pagination.items], page=page, pages=pagination.pages, total=pagination.total)


@bp.route('/<int:box_id>/add-material', methods=['GET', 'POST'])
@login_required
@admin_required
@synchronized(admin=True)
def add_material(box_id):
    box = db.get_or_404(StorageBox, box_id)
    categories, _, _ = category_data()
    if request.method == 'GET':
        if not box.is_active:
            abort(404)
        return render_template('boxes/add_material.html', box=box, categories=categories, request_token=issue_token(current_user.id, box.id), initial_selection=[], global_error=None)
    token = request.form.get('request_token', '')
    wants_json = 'application/json' in request.headers.get('Accept', '')
    rows = []
    message = None
    status = 400
    try:
        receipt_id = verify_token(token, current_user.id, box.id)
        digest = submitted_hash(request.form)
        receipt = db.session.get(BoxFillReceipt, receipt_id)
        if receipt:
            if digest is None or receipt.payload_hash != digest or receipt.user_id != current_user.id or receipt.box_id != box.id:
                message = "Cette demande a déjà été utilisée avec une autre sélection."
                status = 409
            else:
                return fill_success(box, receipt.reference_count, receipt.unit_count, True, wants_json)
        elif not box.is_active:
            message = "Cette boîte n'est plus disponible."
            status = 409
        else:
            rows, lines = parse_selection(request.form, box.id)
            if digest is None or not rows or any(row['error'] for row in rows):
                message = "Vérifie la sélection et les quantités demandées."
            else:
                for item, amount in lines:
                    content(item, box, True).present_quantity += amount
                    db.session.add(BoxEvent(item_id=item.id, item_name_snapshot=item.name, quantity=amount, source_box_id=None, destination_box_id=box.id, source_name_snapshot='Hors boîte', destination_name_snapshot=box.name, user_id=current_user.id))
                count, units = len(lines), sum(amount for _, amount in lines)
                db.session.add(BoxFillReceipt(id=receipt_id, user_id=current_user.id, box_id=box.id, payload_hash=digest, reference_count=count, unit_count=units))
                db.session.commit()
                return fill_success(box, count, units, False, wants_json)
    except ValueError as exc:
        message = str(exc)
    except Exception:
        db.session.rollback()
        raise
    db.session.rollback()
    if not rows:
        rows, _ = parse_selection(request.form, box.id)
    if wants_json:
        return jsonify(ok=False, message=message, rows=rows, request_token=token), status
    return render_template('boxes/add_material.html', box=box, categories=categories, request_token=token, initial_selection=rows, global_error=message), status


def fill_success(box, count, units, replayed, wants_json):
    target = url_for('boxes.detail', box_id=box.id)
    flash(f'{count} références ajoutées, soit {units} unités.', 'success')
    if wants_json:
        return jsonify(ok=True, redirect=target, reference_count=count, unit_count=units, replayed=replayed)
    return redirect(target)

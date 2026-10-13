from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from flask import (abort, current_app, flash, make_response, redirect,
                   render_template, request, send_from_directory, url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from . import bp
from ..admin.decorators import admin_required
from ..extensions import db
from ..models import Borrowing, BorrowingEvent, Category, Item, StockMovement, utcnow
from ..storage import InvalidImage, delete_photo, save_photo

CONDITIONS = ["Bon état", "À contrôler", "À réparer", "Hors service"]


def _category_options():
    result = []

    def visit(category, depth=0):
        result.append((category, f"{'— ' * depth}{category.name}"))
        for child in category.children:
            visit(child, depth + 1)

    roots = Category.query.filter_by(parent_id=None).order_by(Category.name).all()
    for category in roots:
        visit(category)
    return result


def _descendant_ids(category: Category) -> list[int]:
    ids = [category.id]
    for child in category.children:
        ids.extend(_descendant_ids(child))
    return ids


def _int_field(name: str, label: str, minimum: int = 0):
    raw = request.form.get(name, "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label} doit être un nombre entier.")
    if value < minimum:
        raise ValueError(f"{label} doit être supérieur ou égal à {minimum}.")
    return value


def _outstanding_subquery():
    return (
        db.session.query(
            Borrowing.item_id.label("item_id"),
            db.func.sum(Borrowing.quantity_out - Borrowing.quantity_returned).label("outstanding_quantity"),
        )
        .filter(
            Borrowing.item_id.isnot(None),
            Borrowing.quantity_out > Borrowing.quantity_returned,
        )
        .group_by(Borrowing.item_id)
        .subquery()
    )


def _build_inventory_query():
    outstanding = _outstanding_subquery()
    outstanding_quantity = db.func.coalesce(outstanding.c.outstanding_quantity, 0)
    query = Item.query.join(Category).outerjoin(outstanding, outstanding.c.item_id == Item.id)
    search = request.args.get("q", "").strip()[:120]
    if search:
        pattern = f"%{search}%"
        # No location or condition filtering: search is restricted to useful identifiers/text.
        query = query.filter(or_(Item.name.ilike(pattern), Item.description.ilike(pattern), Category.name.ilike(pattern)))

    category_raw = request.args.get("category", "").strip()
    selected_category = None
    if category_raw:
        try:
            selected_category = db.session.get(Category, int(category_raw))
        except (TypeError, ValueError):
            selected_category = None
        if selected_category:
            query = query.filter(Item.category_id.in_(_descendant_ids(selected_category)))

    availability = request.args.get("availability", "").strip()
    if availability == "available":
        query = query.filter(Item.quantity > 0, outstanding_quantity == 0)
    elif availability == "partial":
        query = query.filter(Item.quantity > 0, outstanding_quantity > 0)
    elif availability == "out":
        query = query.filter(Item.quantity == 0, outstanding_quantity > 0)
    elif availability == "empty":
        query = query.filter(Item.quantity == 0, outstanding_quantity == 0)
    else:
        availability = ""

    quantity_min_raw = request.args.get("quantity_min", "").strip()
    quantity_min = None
    if quantity_min_raw:
        try:
            quantity_min = int(quantity_min_raw)
        except ValueError:
            quantity_min = None
        if quantity_min is not None and 0 <= quantity_min <= 100000:
            query = query.filter(Item.quantity >= quantity_min)
        else:
            quantity_min = None

    return query, search, selected_category, availability, quantity_min


def _render_inventory_results(items, outstanding_by_item, *, search, selected_category, availability, quantity_min):
    has_filters = bool(search or selected_category or availability or quantity_min is not None)
    return render_template(
        "inventory/_item_results.html",
        items=items,
        outstanding_by_item=outstanding_by_item,
        has_filters=has_filters,
    )


@bp.get("/")
@login_required
def index():
    query, search, selected_category, availability, quantity_min = _build_inventory_query()
    items = query.order_by(Item.name.asc()).all()
    if items:
        rows = (
            db.session.query(
                Borrowing.item_id,
                db.func.sum(Borrowing.quantity_out - Borrowing.quantity_returned),
            )
            .filter(
                Borrowing.item_id.in_([item.id for item in items]),
                Borrowing.quantity_out > Borrowing.quantity_returned,
            )
            .group_by(Borrowing.item_id)
            .all()
        )
        outstanding_by_item = {item_id: int(quantity or 0) for item_id, quantity in rows}
    else:
        outstanding_by_item = {}

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        response = make_response(_render_inventory_results(
            items, outstanding_by_item, search=search,
            selected_category=selected_category, availability=availability,
            quantity_min=quantity_min,
        ))
        response.headers["Cache-Control"] = "no-store"
        return response

    total_references = Item.query.count()
    total_units = db.session.query(db.func.coalesce(db.func.sum(Item.quantity), 0)).scalar()
    attention_count = Item.query.filter(Item.condition.in_(["À contrôler", "À réparer", "Hors service"])).count()
    return render_template(
        "inventory/index.html",
        items=items,
        outstanding_by_item=outstanding_by_item,
        categories=_category_options(),
        selected_category=selected_category,
        search=search,
        availability=availability,
        quantity_min=quantity_min if quantity_min is not None else "",
        has_filters=bool(search or selected_category or availability or quantity_min is not None),
        total_references=total_references,
        total_units=total_units,
        attention_count=attention_count,
    )


@bp.get("/loans")
@login_required
def loans():
    active_loans = (
        Borrowing.query
        .filter(Borrowing.quantity_out > Borrowing.quantity_returned)
        .filter(Borrowing.item_id.isnot(None))
        .order_by(Borrowing.created_at.asc(), Borrowing.id.asc())
        .all()
    )
    recent_events = BorrowingEvent.query.order_by(BorrowingEvent.created_at.desc(), BorrowingEvent.id.desc()).limit(100).all()
    return render_template(
        "inventory/loans.html",
        active_loans=active_loans,
        recent_events=recent_events,
        total_outstanding=sum(loan.remaining_quantity for loan in active_loans),
    )


@bp.route("/items/new", methods=["GET", "POST"])
@login_required
def create_item():
    categories = _category_options()
    form = request.form if request.method == "POST" else {}
    if not categories:
        flash("Commence par créer une catégorie pour organiser le matériel.", "info")
        return redirect(url_for("inventory.categories"))

    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            category_id = _int_field("category_id", "La catégorie", minimum=1)
            quantity = _int_field("quantity", "La quantité", minimum=0)
            unit = "unité"
            location = request.form.get("location", "").strip()
            condition = request.form.get("condition", "Bon état").strip()
            if not name or len(name) > 160:
                raise ValueError("Le nom est obligatoire et ne doit pas dépasser 160 caractères.")
            if len(description) > 5000:
                raise ValueError("La description ne doit pas dépasser 5 000 caractères.")
            if len(location) > 160:
                raise ValueError("L’emplacement ne doit pas dépasser 160 caractères.")
            if condition not in CONDITIONS:
                raise ValueError("Choisis un état proposé dans la liste.")
            category = db.session.get(Category, category_id)
            if category is None:
                raise ValueError("La catégorie sélectionnée n’existe pas.")
            photo = request.files.get("photo")
            photo_key = save_photo(photo)
        except (ValueError, InvalidImage) as exc:
            flash(str(exc), "error")
            return render_template("inventory/item_form.html", item=None, categories=categories, conditions=CONDITIONS, form=form), 400

        item = Item(
            name=name, description=description, category=category,
            quantity=quantity, unit=unit, location=location,
            condition=condition, photo_key=photo_key,
            created_by=current_user, updated_by=current_user,
        )
        db.session.add(item)
        db.session.flush()
        db.session.add(StockMovement(item=item, item_name_snapshot=item.name, user=current_user, delta=quantity, quantity_after=quantity, note="Stock initial"))
        db.session.commit()
        flash(f"« {item.name} » a été ajouté à l’inventaire.", "success")
        return redirect(url_for("inventory.item_detail", item_id=item.id))

    return render_template("inventory/item_form.html", item=None, categories=categories, conditions=CONDITIONS, form=form)


@bp.get("/items/<int:item_id>")
@login_required
def item_detail(item_id):
    item = db.get_or_404(Item, item_id)
    active_borrowings = (
        Borrowing.query.filter_by(item_id=item.id)
        .filter(Borrowing.quantity_out > Borrowing.quantity_returned)
        .order_by(Borrowing.created_at.asc(), Borrowing.id.asc()).all()
    )
    recent_loan_events = (
        BorrowingEvent.query.filter_by(item_id=item.id)
        .order_by(BorrowingEvent.created_at.desc(), BorrowingEvent.id.desc()).limit(30).all()
    )
    outstanding_quantity = sum(loan.remaining_quantity for loan in active_borrowings)
    return render_template(
        "inventory/item_detail.html", item=item, conditions=CONDITIONS,
        active_borrowings=active_borrowings,
        outstanding_quantity=outstanding_quantity,
        recent_loan_events=recent_loan_events,
    )


@bp.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_item(item_id):
    item = db.get_or_404(Item, item_id)
    categories = _category_options()
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        old_photo_key = item.photo_key
        try:
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            category_id = _int_field("category_id", "La catégorie", minimum=1)
            unit = "unité"
            location = request.form.get("location", "").strip()
            condition = request.form.get("condition", "Bon état").strip()
            if not name or len(name) > 160:
                raise ValueError("Le nom est obligatoire et ne doit pas dépasser 160 caractères.")
            if len(description) > 5000:
                raise ValueError("La description ne doit pas dépasser 5 000 caractères.")
            if len(location) > 160:
                raise ValueError("L’emplacement ne doit pas dépasser 160 caractères.")
            if condition not in CONDITIONS:
                raise ValueError("Choisis un état proposé dans la liste.")
            category = db.session.get(Category, category_id)
            if category is None:
                raise ValueError("La catégorie sélectionnée n’existe pas.")
            new_photo = request.files.get("photo")
            new_photo_key = save_photo(new_photo) if new_photo and new_photo.filename else None
        except (ValueError, InvalidImage) as exc:
            flash(str(exc), "error")
            return render_template("inventory/item_form.html", item=item, categories=categories, conditions=CONDITIONS, form=form), 400

        item.name = name
        item.description = description
        item.category = category
        item.unit = unit
        item.location = location
        item.condition = condition
        item.updated_by = current_user
        if new_photo_key:
            item.photo_key = new_photo_key
        db.session.commit()
        if new_photo_key:
            delete_photo(old_photo_key)
        flash("La fiche matériel a été mise à jour.", "success")
        return redirect(url_for("inventory.item_detail", item_id=item.id))
    return render_template("inventory/item_form.html", item=item, categories=categories, conditions=CONDITIONS, form=form)


@bp.post("/items/<int:item_id>/adjust-stock")
@login_required
def adjust_stock(item_id):
    """Record a new outing. Returns must be linked to a particular active loan."""
    item = db.get_or_404(Item, item_id)
    movement_type = request.form.get("movement_type", "emprunt").strip().lower()
    if movement_type == "retour":
        flash("Pour enregistrer un retour, sélectionne l’emprunt correspondant dans « Matériel sorti ».", "error")
        return redirect(url_for("inventory.loans"))
    if movement_type not in {"", "emprunt"}:
        flash("Type de mouvement non reconnu.", "error")
        return redirect(url_for("inventory.item_detail", item_id=item.id))

    raw_amount = request.form.get("quantity", "").strip()
    if raw_amount:
        try:
            amount = int(raw_amount)
        except (TypeError, ValueError):
            flash("Saisis une quantité entière à sortir du stock.", "error")
            return redirect(url_for("inventory.item_detail", item_id=item.id))
    else:
        # Compatibility for a legacy signed-delta form: negative means outing.
        # A positive delta must never silently become another outing; returns
        # must identify the active loan they close or reduce.
        try:
            legacy_delta = int(request.form.get("delta", ""))
        except (TypeError, ValueError):
            flash("Saisis une quantité entière à sortir du stock.", "error")
            return redirect(url_for("inventory.item_detail", item_id=item.id))
        if legacy_delta >= 0:
            flash("Un retour doit être associé à un emprunt dans « Matériel sorti ».", "error")
            return redirect(url_for("inventory.loans"))
        amount = -legacy_delta
    if amount < 1 or amount > 100000:
        flash("La quantité doit être comprise entre 1 et 100 000.", "error")
        return redirect(url_for("inventory.item_detail", item_id=item.id))
    note = request.form.get("note", "").strip()
    if len(note) > 240:
        flash("Le commentaire ne doit pas dépasser 240 caractères.", "error")
        return redirect(url_for("inventory.item_detail", item_id=item.id))

    # The guarded arithmetic update avoids negative stock when two volunteers act at once.
    updated = Item.query.filter_by(id=item.id).filter(Item.quantity >= amount).update(
        {Item.quantity: Item.quantity - amount, Item.updated_by_id: current_user.id, Item.updated_at: utcnow()},
        synchronize_session=False,
    )
    if updated != 1:
        db.session.rollback()
        flash("La quantité disponible est insuffisante ou le stock vient de changer. Recharge la page puis réessaie.", "error")
        return redirect(url_for("inventory.item_detail", item_id=item.id))

    db.session.refresh(item)
    borrowing = Borrowing(
        item=item,
        item_name_snapshot=item.name,
        user=current_user,
        quantity_out=amount,
        quantity_returned=0,
        note=note,
    )
    db.session.add(borrowing)
    db.session.flush()
    db.session.add(BorrowingEvent(
        borrowing=borrowing, item=item, item_name_snapshot=item.name,
        user=current_user, event_type="sortie", quantity=amount, note=note,
    ))
    movement_note = f"Emprunt · {note}" if note else "Emprunt"
    db.session.add(StockMovement(
        item=item, item_name_snapshot=item.name, user=current_user,
        delta=-amount, quantity_after=item.quantity, note=movement_note,
    ))
    db.session.commit()
    flash(f"Sortie enregistrée : {amount} unité{'s' if amount > 1 else ''}. Il reste {item.quantity} unité{'s' if item.quantity > 1 else ''} disponible{'s' if item.quantity > 1 else ''}.", "success")
    return redirect(url_for("inventory.item_detail", item_id=item.id))


@bp.post("/loans/<int:loan_id>/return")
@login_required
def return_loan(loan_id):
    loan = Borrowing.query.filter_by(id=loan_id).with_for_update().first_or_404()
    if loan.item_id is None or loan.item is None:
        flash("L’objet associé à cet emprunt n’existe plus. L’historique a été conservé.", "error")
        return redirect(url_for("inventory.loans"))
    item = Item.query.filter_by(id=loan.item_id).with_for_update().first()
    if item is None:
        flash("Le matériel associé à cet emprunt n’existe plus.", "error")
        return redirect(url_for("inventory.loans"))

    try:
        amount = _int_field("quantity", "La quantité à rendre", minimum=1)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("inventory.loans"))
    if amount > loan.remaining_quantity:
        flash(f"Tu ne peux pas rendre plus que les {loan.remaining_quantity} unité(s) encore à l’extérieur.", "error")
        return redirect(url_for("inventory.loans"))
    note = request.form.get("note", "").strip()
    if len(note) > 240:
        flash("Le commentaire ne doit pas dépasser 240 caractères.", "error")
        return redirect(url_for("inventory.loans"))

    item.quantity += amount
    item.updated_by = current_user
    item.updated_at = utcnow()
    loan.quantity_returned += amount
    if loan.remaining_quantity == 0:
        loan.closed_at = utcnow()

    db.session.add(BorrowingEvent(
        borrowing=loan, item=item, item_name_snapshot=loan.item_name_snapshot,
        user=current_user, event_type="retour", quantity=amount, note=note,
    ))
    movement_note = f"Retour · {note}" if note else "Retour d’emprunt"
    db.session.add(StockMovement(
        item=item, item_name_snapshot=loan.item_name_snapshot, user=current_user,
        delta=amount, quantity_after=item.quantity, note=movement_note,
    ))
    db.session.commit()
    if loan.remaining_quantity == 0:
        flash(f"Retour complet enregistré pour « {loan.item_name_snapshot} ».", "success")
    else:
        flash(f"Retour partiel enregistré. Il reste {loan.remaining_quantity} unité(s) à l’extérieur.", "success")
    return redirect(url_for("inventory.loans"))


@bp.post("/items/<int:item_id>/delete")
@login_required
@admin_required
def delete_item(item_id):
    item = db.get_or_404(Item, item_id)
    open_loan = (
        Borrowing.query.filter_by(item_id=item.id)
        .filter(Borrowing.quantity_out > Borrowing.quantity_returned)
        .first()
    )
    if open_loan:
        flash(
            f"Impossible de supprimer « {item.name} » : du matériel est encore à l’extérieur. Enregistre d’abord tous les retours.",
            "error",
        )
        return redirect(url_for("inventory.item_detail", item_id=item.id))

    item_id_value = item.id
    item_name = item.name
    photo_key = item.photo_key
    # Freeze names in every historical record before the live Item row is removed.
    StockMovement.query.filter_by(item_id=item_id_value).filter(
        or_(StockMovement.item_name_snapshot.is_(None), StockMovement.item_name_snapshot == "")
    ).update({StockMovement.item_name_snapshot: item_name}, synchronize_session=False)
    Borrowing.query.filter_by(item_id=item_id_value).filter(
        or_(Borrowing.item_name_snapshot.is_(None), Borrowing.item_name_snapshot == "")
    ).update({Borrowing.item_name_snapshot: item_name}, synchronize_session=False)
    BorrowingEvent.query.filter_by(item_id=item_id_value).filter(
        or_(BorrowingEvent.item_name_snapshot.is_(None), BorrowingEvent.item_name_snapshot == "")
    ).update({BorrowingEvent.item_name_snapshot: item_name}, synchronize_session=False)

    db.session.delete(item)
    db.session.commit()

    # Only delete the photo object if no other inventory record still refers to it.
    still_used = Item.query.filter_by(photo_key=photo_key).first() if photo_key else None
    if photo_key and still_used is None:
        delete_photo(photo_key)
    flash(f"« {item_name} » a été supprimé. L’historique des mouvements et emprunts est conservé.", "success")
    return redirect(url_for("inventory.index"))


def _category_parent_from_raw(parent_raw: str) -> Category | None:
    """Resolve an optional parent ID and reject invalid references."""
    if not parent_raw:
        return None
    try:
        parent_id = int(parent_raw)
    except (TypeError, ValueError):
        raise ValueError("La catégorie parente sélectionnée est invalide.")
    parent = db.session.get(Category, parent_id)
    if parent is None:
        raise ValueError("La catégorie parente sélectionnée n’existe pas.")
    return parent


def _sibling_name_exists(name: str, parent_id: int | None, exclude_id: int | None = None) -> bool:
    """Category names must be unique among siblings, not across the whole tree."""
    query = Category.query.filter(db.func.lower(Category.name) == name.casefold())
    if parent_id is None:
        query = query.filter(Category.parent_id.is_(None))
    else:
        query = query.filter(Category.parent_id == parent_id)
    if exclude_id is not None:
        query = query.filter(Category.id != exclude_id)
    return query.first() is not None


@bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    """Browse and create nodes in a user-defined, arbitrarily deep category tree."""
    name_value = ""
    parent_raw = request.args.get("parent_id", "").strip()
    if request.method == "POST":
        name_value = request.form.get("name", "").strip()
        parent_raw = request.form.get("parent_id", "").strip()
        try:
            if not name_value or len(name_value) > 100:
                raise ValueError("Le nom de la catégorie est obligatoire (100 caractères maximum).")
            parent = _category_parent_from_raw(parent_raw)
            if _sibling_name_exists(name_value, parent.id if parent else None):
                raise ValueError("Une catégorie portant ce nom existe déjà à ce niveau.")
            category = Category(name=name_value, parent=parent)
            db.session.add(category)
            db.session.commit()
            flash(f"Branche « {category.full_path} » créée.", "success")
            return redirect(url_for("inventory.categories"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")

    selected_parent_id = None
    if parent_raw:
        try:
            requested_parent = db.session.get(Category, int(parent_raw))
            if requested_parent is not None:
                selected_parent_id = requested_parent.id
            else:
                flash("La catégorie parente demandée n’existe plus.", "error")
        except (TypeError, ValueError):
            flash("La catégorie parente demandée est invalide.", "error")

    roots = Category.query.filter_by(parent_id=None).order_by(Category.name).all()
    return render_template(
        "inventory/categories.html",
        roots=roots,
        categories=_category_options(),
        selected_parent_id=selected_parent_id,
        name_value=name_value,
    )


@bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@login_required
def edit_category(category_id):
    category = db.get_or_404(Category, category_id)
    form_name = category.name
    selected_parent_id = category.parent_id

    # A node cannot be moved beneath itself or any of its descendants.
    forbidden_parent_ids = set(_descendant_ids(category))
    available_parents = [
        (candidate, label)
        for candidate, label in _category_options()
        if candidate.id not in forbidden_parent_ids
    ]

    if request.method == "POST":
        form_name = request.form.get("name", "").strip()
        parent_raw = request.form.get("parent_id", "").strip()
        try:
            if not form_name or len(form_name) > 100:
                raise ValueError("Le nom de la catégorie est obligatoire (100 caractères maximum).")
            parent = _category_parent_from_raw(parent_raw)
            if parent and parent.id in forbidden_parent_ids:
                raise ValueError("Déplacement impossible : une branche ne peut pas être placée sous elle-même ou sous l’une de ses sous-catégories.")
            if _sibling_name_exists(form_name, parent.id if parent else None, exclude_id=category.id):
                raise ValueError("Une catégorie portant ce nom existe déjà à ce niveau.")

            category.name = form_name
            category.parent = parent
            db.session.commit()
            flash(f"Catégorie mise à jour : « {category.full_path} ».", "success")
            return redirect(url_for("inventory.categories"))
        except ValueError as exc:
            db.session.rollback()
            # Refresh model state after rollback so the form reflects the saved node.
            category = db.session.get(Category, category_id)
            flash(str(exc), "error")
        if parent_raw:
            try:
                selected_parent_id = int(parent_raw)
            except ValueError:
                selected_parent_id = category.parent_id
        else:
            selected_parent_id = None

    return render_template(
        "inventory/category_form.html",
        category=category,
        categories=available_parents,
        form_name=form_name,
        selected_parent_id=selected_parent_id,
    )


@bp.post("/categories/<int:category_id>/delete")
@login_required
def delete_category(category_id):
    category = db.get_or_404(Category, category_id)
    # Never cascade-delete stock references or child branches by accident.
    if category.children or category.items:
        flash(
            "Cette catégorie n’est pas vide. Déplace d’abord ses objets et ses sous-catégories avant de la supprimer.",
            "error",
        )
        return redirect(url_for("inventory.categories"))

    path = category.full_path
    db.session.delete(category)
    db.session.commit()
    flash(f"Catégorie « {path} » supprimée.", "success")
    return redirect(url_for("inventory.categories"))


@bp.get("/items.csv")
@login_required
def export_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Nom", "Catégorie", "Description", "Quantité", "Unité", "Emplacement", "État", "Modifié le"])
    for item in Item.query.join(Category).order_by(Item.name).all():
        writer.writerow([
            item.name, item.category.full_path, item.description,
            item.quantity, item.unit, item.location, item.condition,
            item.updated_at.isoformat() if item.updated_at else "",
        ])
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=inventaire-club.csv"
    return response


@bp.get("/uploads/<path:filename>")
@login_required
def uploaded_photo(filename):
    # Photos remain behind authentication in local-storage mode. S3 mode uses
    # expiring signed URLs (or an explicitly configured public media domain).
    root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    target = (root / filename).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        abort(404)
    if not target.is_file():
        abort(404)
    return send_from_directory(root, filename, max_age=3600)

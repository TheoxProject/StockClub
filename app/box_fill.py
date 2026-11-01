"""Queries and validation for atomic, idempotent box filling."""
import hashlib
import json
import uuid

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import aliased

from .extensions import db
from .models import BoxContent, Category, Item
from .storage import photo_url


def issue_token(user_id, box_id):
    return URLSafeTimedSerializer(current_app.secret_key, salt="box-fill-v1").dumps(
        {"id": str(uuid.uuid4()), "user": user_id, "box": box_id}
    )


def verify_token(token, user_id, box_id):
    try:
        data = URLSafeTimedSerializer(current_app.secret_key, salt="box-fill-v1").loads(token, max_age=86400)
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise ValueError
        if type(data.get("user")) is not int or type(data.get("box")) is not int:
            raise ValueError
        ident = str(uuid.UUID(data["id"]))
        if data["id"] != ident or data["user"] != user_id or data["box"] != box_id:
            raise ValueError
        return ident
    except (BadSignature, SignatureExpired, ValueError, TypeError, KeyError):
        raise ValueError("Cette demande a expiré ou est invalide. Recharge la page.")


def stock_query(box_id, *, available_only=False):
    totals = db.session.query(BoxContent.item_id.label("item_id"), db.func.sum(BoxContent.present_quantity).label("total")).group_by(BoxContent.item_id).subquery()
    destination = aliased(BoxContent)
    outside = Item.quantity - db.func.coalesce(totals.c.total, 0)
    present = db.func.coalesce(destination.present_quantity, 0)
    query = (db.session.query(Item, outside.label("available"), present.label("present"))
            .outerjoin(totals, totals.c.item_id == Item.id)
            .outerjoin(destination, db.and_(destination.item_id == Item.id, destination.box_id == box_id)))
    return query.filter(outside > 0) if available_only else query


def category_data():
    categories = Category.query.order_by(Category.name, Category.id).all()
    children = {}
    for category in categories:
        children.setdefault(category.parent_id, []).append(category)
    options = []
    paths = {}
    def visit(category, depth, parents):
        options.append((category, "— " * depth + category.name))
        paths[category.id] = " › ".join(parents + [category.name])
        for child in children.get(category.id, []):
            visit(child, depth + 1, parents + [category.name])
    for root in children.get(None, []):
        visit(root, 0, [])
    return options, paths, children


def descendants(category_id, children):
    result = []
    pending = [category_id]
    while pending:
        ident = pending.pop()
        result.append(ident)
        pending.extend(child.id for child in children.get(ident, []))
    return result


def option_row(item, available, present, paths):
    return dict(id=item.id, name=item.name, category=paths.get(item.category_id, ""),
                photo_url=photo_url(item.photo_key) if item.photo_key else None,
                available=int(available), present=int(present))


def parse_selection(form, box_id):
    raw_ids = form.getlist("selected")
    parsed = []
    ids = []
    for raw in raw_ids:
        ident = None
        try:
            value = int(raw)
            if str(value) == raw and 0 < value <= 9223372036854775807:
                ident = value
                ids.append(value)
        except (ValueError, TypeError):
            pass
        parsed.append((raw, ident, form.get("quantity_" + raw, ""), form.get("label_" + raw, "")[:160]))
    stock = {item.id: (item, int(outside), int(present)) for item, outside, present in stock_query(box_id).filter(Item.id.in_(ids)).all()} if ids else {}
    rows, lines = [], []
    duplicate = len(raw_ids) != len(set(raw_ids))
    for raw, ident, quantity, label in parsed:
        item, outside, present = stock.get(ident, (None, 0, 0))
        error = None
        amount = None
        if ident is None or duplicate:
            error = "Identifiant invalide ou en double."
        elif item is None:
            error = "Cet objet n'existe plus."
        else:
            try:
                if not quantity or not quantity.isascii() or not quantity.isdecimal():
                    raise ValueError
                amount = int(quantity)
                if amount <= 0 or amount > 1000000:
                    raise ValueError
            except ValueError:
                error = "La quantité doit être un entier entre 1 et 1 000 000."
            if not error and amount > outside:
                error = f"{amount} demandés, seulement {max(0, outside)} disponibles."
        rows.append(dict(id=ident if ident is not None else raw, name=item.name if item else (label or "Objet indisponible"), quantity=quantity, available=max(0, outside), present=present, error=error))
        if not error:
            lines.append((item, amount))
    return rows, lines


def submitted_hash(form):
    raw_ids = form.getlist("selected")
    if not raw_ids or len(raw_ids) != len(set(raw_ids)):
        return None
    pairs = []
    for raw in raw_ids:
        quantity = form.get("quantity_" + raw, "")
        try:
            ident = int(raw)
            if str(ident) != raw or not 0 < ident <= 9223372036854775807:
                return None
            if not quantity or not quantity.isascii() or not quantity.isdecimal():
                return None
            amount = int(quantity)
            if not 0 < amount <= 1000000:
                return None
        except (ValueError, TypeError):
            return None
        pairs.append((ident, amount))
    return hashlib.sha256(json.dumps(sorted(pairs), separators=(",", ":")).encode()).hexdigest()

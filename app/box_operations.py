from .extensions import db
from .models import BoxContent, Borrowing, BorrowingEvent, Item, StockMovement, StorageBox, utcnow


def get_box(raw):
    if not raw:
        return None
    try:
        box_id = int(raw)
    except (ValueError, TypeError):
        raise ValueError("Provenance ou destination invalide.")
    if box_id < 1 or box_id > 9223372036854775807:
        raise ValueError("Provenance ou destination invalide.")
    box = StorageBox.query.filter_by(id=box_id, is_active=True).first()
    if box is None:
        raise ValueError("Cette boîte n'existe plus. Actualise la page.")
    return box


def content(item, box, create=False):
    if box is None:
        return None
    row = BoxContent.query.filter_by(item_id=item.id, box_id=box.id).first()
    if row is None and create:
        row = BoxContent(item=item, box=box, present_quantity=0)
        db.session.add(row)
    return row


def available(item, box):
    if box:
        row = content(item, box)
        return row.present_quantity if row else 0
    total = db.session.query(db.func.coalesce(db.func.sum(BoxContent.present_quantity), 0)).filter_by(item_id=item.id).scalar()
    return item.quantity - int(total)


def distribution_for(item):
    contents = {row.box_id: row for row in BoxContent.query.filter_by(item_id=item.id).all()}
    loans = Borrowing.query.filter_by(item_id=item.id).filter(Borrowing.box_id.isnot(None), Borrowing.quantity_out > Borrowing.quantity_returned + Borrowing.quantity_lost + Borrowing.quantity_consumed).all()
    outstanding = {}
    for loan in loans:
        outstanding[loan.box_id] = outstanding.get(loan.box_id, 0) + loan.remaining_quantity
    ids = set(contents) | set(outstanding)
    boxes = StorageBox.query.filter(StorageBox.id.in_(ids), StorageBox.is_active.is_(True)).order_by(StorageBox.name).all() if ids else []
    rows = []
    for box in boxes:
        present = contents[box.id].present_quantity if box.id in contents else 0
        out = outstanding.get(box.id, 0)
        if present or out:
            rows.append(dict(box=box, present=present, outstanding=out, assigned=present + out))
    return rows


def sources_for(item):
    rows = distribution_for(item)
    outside = available(item, None)
    return ([dict(id="", name="Hors boîte", available=outside)] +
            [dict(id=row['box'].id, name=row['box'].name, available=row['present']) for row in rows]), rows, outside


def checkout(item, box, amount, user, note='', group=None, maximum=100000):
    if amount <= 0 or amount > maximum or available(item, box) < amount or item.quantity < amount:
        raise ValueError("La quantité disponible est insuffisante ou le stock vient de changer. Recharge la page puis réessaie.")
    if box:
        content(item, box).present_quantity -= amount
    item.quantity -= amount
    item.updated_by = user
    item.updated_at = utcnow()
    loan = Borrowing(item=item, item_name_snapshot=item.name, user=user, quantity_out=amount, quantity_returned=0, note=note, box=box, box_name_snapshot=box.name if box else '', group=group)
    db.session.add(loan)
    db.session.flush()
    db.session.add(BorrowingEvent(borrowing=loan, item=item, item_name_snapshot=item.name, user=user, event_type='sortie', quantity=amount, note=note))
    db.session.add(StockMovement(item=item, item_name_snapshot=item.name, user=user, delta=-amount, quantity_after=item.quantity, note=f'Emprunt · {note}' if note else 'Emprunt', movement_type='emprunt', total_delta=0, borrowing_id=loan.id, box=box, box_name_snapshot=loan.box_name_snapshot))
    return loan


def return_loan(loan, amount, user, note=''):
    item = loan.item
    if not item or amount <= 0 or amount > loan.remaining_quantity or item.quantity + amount > 1000000:
        raise ValueError("Retour impossible : quantité ou stock modifié. Actualise la page.")
    if loan.box_id:
        box = StorageBox.query.filter_by(id=loan.box_id, is_active=True).first()
        if not box:
            raise ValueError("La boîte d'origine n'est plus disponible.")
        content(item, box, True).present_quantity += amount
    item.quantity += amount
    item.updated_by = user
    item.updated_at = utcnow()
    loan.quantity_returned += amount
    if not loan.remaining_quantity:
        loan.closed_at = utcnow()
    db.session.add(BorrowingEvent(borrowing=loan, item=item, item_name_snapshot=loan.item_name_snapshot, user=user, event_type='retour', quantity=amount, note=note))
    db.session.add(StockMovement(item=item, item_name_snapshot=loan.item_name_snapshot, user=user, delta=amount, quantity_after=item.quantity, note=f'Retour · {note}' if note else 'Retour d’emprunt', movement_type='retour', total_delta=0, borrowing_id=loan.id, box_id=loan.box_id, box_name_snapshot=loan.box_name_snapshot))

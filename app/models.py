from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, event
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


def utcnow():
    return datetime.now(timezone.utc)


class BootstrapLock(db.Model):
    """Singleton row used to serialize first-administrator initialization."""

    __tablename__ = "bootstrap_locks"
    __table_args__ = (CheckConstraint("id = 1", name="ck_bootstrap_locks_singleton_id"),)

    id = db.Column(db.Integer, primary_key=True)
    lock_version = db.Column(db.Integer, nullable=False, default=0, server_default="0")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    login = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active_user = db.Column(db.Boolean, nullable=False, default=True)
    role = db.Column(db.String(20), nullable=False, default="member", server_default="member", index=True)
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    locked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def role_label(self) -> str:
        return "Administrateur" if self.is_admin else "Membre"

    @property
    def is_active(self):
        return self.is_active_user

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    parent = db.relationship(
        "Category",
        remote_side=[id],
        back_populates="children",
        foreign_keys=[parent_id],
    )
    children = db.relationship(
        "Category",
        back_populates="parent",
        cascade="save-update, merge",
        order_by="Category.name",
    )
    items = db.relationship("Item", back_populates="category", lazy="select")

    @property
    def full_path(self) -> str:
        parts = []
        current = self
        while current is not None:
            parts.append(current.name)
            current = current.parent
        return " › ".join(reversed(parts))


class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False, default="")
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    unit = db.Column(db.String(40), nullable=False, default="unité")
    location = db.Column(db.String(160), nullable=False, default="")
    condition = db.Column(db.String(30), nullable=False, default="Bon état")
    photo_key = db.Column(db.String(500), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    category = db.relationship("Category", back_populates="items")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    # Movements and borrowings are deliberately preserved if an administrator
    # deletes an item. Their item foreign keys become NULL and their snapshots
    # keep the former item's name for historical traceability.
    movements = db.relationship(
        "StockMovement",
        back_populates="item",
        order_by="StockMovement.created_at.desc()",
    )
    borrowings = db.relationship(
        "Borrowing",
        back_populates="item",
        order_by="Borrowing.created_at.desc()",
    )
    borrowing_events = db.relationship(
        "BorrowingEvent",
        back_populates="item",
        order_by="BorrowingEvent.created_at.desc()",
    )

    @property
    def condition_css(self) -> str:
        return {
            "Bon état": "good",
            "À contrôler": "check",
            "À réparer": "repair",
            "Hors service": "broken",
        }.get(self.condition, "neutral")


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id", ondelete="SET NULL"), nullable=True, index=True)
    item_name_snapshot = db.Column(db.String(160), nullable=False, default="Objet supprimé")
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    delta = db.Column(db.Integer, nullable=False)
    quantity_after = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(240), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    item = db.relationship("Item", back_populates="movements")
    user = db.relationship("User")


@event.listens_for(StockMovement, "before_insert")
def _stock_movement_snapshot(mapper, connection, target):
    if not target.item_name_snapshot and target.item is not None:
        target.item_name_snapshot = target.item.name
    elif not target.item_name_snapshot:
        target.item_name_snapshot = "Objet supprimé"


class Borrowing(db.Model):
    """One loan initiated by a connected volunteer; kept after the item is deleted."""

    __tablename__ = "borrowings"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id", ondelete="SET NULL"), nullable=True, index=True)
    item_name_snapshot = db.Column(db.String(160), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    quantity_out = db.Column(db.Integer, nullable=False)
    quantity_returned = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    note = db.Column(db.String(240), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    closed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    item = db.relationship("Item", back_populates="borrowings")
    user = db.relationship("User")
    events = db.relationship("BorrowingEvent", back_populates="borrowing", order_by="BorrowingEvent.created_at.asc()")

    @property
    def remaining_quantity(self) -> int:
        return max(0, self.quantity_out - self.quantity_returned)

    @property
    def is_open(self) -> bool:
        return self.remaining_quantity > 0


class BorrowingEvent(db.Model):
    """Immutable ledger entry recording an actual outing or return."""

    __tablename__ = "borrowing_events"

    id = db.Column(db.Integer, primary_key=True)
    borrowing_id = db.Column(db.Integer, db.ForeignKey("borrowings.id", ondelete="SET NULL"), nullable=True, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("items.id", ondelete="SET NULL"), nullable=True, index=True)
    item_name_snapshot = db.Column(db.String(160), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = db.Column(db.String(20), nullable=False)  # "sortie" or "retour"
    quantity = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(240), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    borrowing = db.relationship("Borrowing", back_populates="events")
    item = db.relationship("Item", back_populates="borrowing_events")
    user = db.relationship("User")


class Invitation(db.Model):
    """One-use invitation. Only a hash of the token is persisted."""

    __tablename__ = "invitations"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_by = db.relationship("User", foreign_keys=[created_by_id])

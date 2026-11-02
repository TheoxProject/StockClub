from alembic import op
import sqlalchemy as sa

revision = "0009_consumables"
down_revision = "0008_box_fill_receipts"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("items") as batch:
        batch.add_column(sa.Column("is_consumable", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("consumption_threshold", sa.Integer(), nullable=True))
    with op.batch_alter_table("borrowings") as batch:
        batch.add_column(sa.Column("quantity_consumed", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("consumptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="SET NULL")),
        sa.Column("borrowing_id", sa.Integer(), sa.ForeignKey("borrowings.id", ondelete="SET NULL")),
        sa.Column("box_id", sa.Integer(), sa.ForeignKey("storage_boxes.id", ondelete="SET NULL")),
        sa.Column("original_id", sa.Integer(), sa.ForeignKey("consumptions.id", ondelete="RESTRICT"), unique=True),
        sa.Column("restored_outside", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("item_name_snapshot", sa.String(160), nullable=False),
        sa.Column("box_name_snapshot", sa.String(160), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_consumptions_item_id", "consumptions", ["item_id"])
    op.create_table("consumable_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM consumptions")).scalar() or op.get_bind().execute(sa.text("SELECT COUNT(*) FROM consumable_receipts")).scalar():
        raise RuntimeError("Retour impossible : historique de consommables présent.")
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM items WHERE is_consumable = :enabled OR consumption_threshold IS NOT NULL"), {"enabled": True}).scalar():
        raise RuntimeError("Retour impossible : des paramètres de consommables sont configurés.")
    op.drop_table("consumable_receipts")
    op.drop_index("ix_consumptions_item_id", table_name="consumptions")
    op.drop_table("consumptions")
    with op.batch_alter_table("borrowings") as batch:
        batch.drop_column("quantity_consumed")
    with op.batch_alter_table("items") as batch:
        batch.drop_column("consumption_threshold")
        batch.drop_column("is_consumable")

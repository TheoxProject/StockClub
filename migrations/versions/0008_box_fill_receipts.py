from alembic import op
import sqlalchemy as sa

revision = "0008_box_fill_receipts"
down_revision = "0007_stock_boxes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "box_fill_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("box_id", sa.Integer, sa.ForeignKey("storage_boxes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("reference_count", sa.Integer, nullable=False),
        sa.Column("unit_count", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM box_fill_receipts")).scalar():
        raise RuntimeError("Retour impossible : des reçus protègent les opérations contre les doublons.")
    op.drop_table("box_fill_receipts")

"""Stock adjustments and loan write-offs.

Revision ID: 0006_stock_adjustments
Revises: 0005_admin_bootstrap
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_stock_adjustments"
down_revision = "0005_admin_bootstrap"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("borrowings") as batch:
        batch.add_column(sa.Column("quantity_lost", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("stock_movements") as batch:
        batch.add_column(sa.Column("movement_type", sa.String(30), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("total_delta", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reason", sa.String(30), nullable=False, server_default=""))
        batch.add_column(sa.Column("borrowing_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_stock_movements_borrowing_id", "borrowings", ["borrowing_id"], ["id"], ondelete="SET NULL")


def downgrade():
    connection = op.get_bind()
    has_operations = connection.execute(sa.text("SELECT COUNT(*) FROM stock_movements WHERE movement_type IN ('ajout', 'retrait', 'perte_emprunt', 'casse_emprunt')")).scalar()
    has_losses = connection.execute(sa.text("SELECT COUNT(*) FROM borrowings WHERE quantity_lost <> 0")).scalar()
    if has_operations or has_losses:
        raise RuntimeError("Retour à la version 0005 impossible après un ajustement ou une perte : l'historique serait perdu.")
    with op.batch_alter_table("stock_movements") as batch:
        batch.drop_constraint("fk_stock_movements_borrowing_id", type_="foreignkey")
        batch.drop_column("borrowing_id")
        batch.drop_column("reason")
        batch.drop_column("total_delta")
        batch.drop_column("movement_type")
    with op.batch_alter_table("borrowings") as batch:
        batch.drop_column("quantity_lost")

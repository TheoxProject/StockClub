from alembic import op
import sqlalchemy as sa

revision = "0007_stock_boxes"
down_revision = "0006_stock_adjustments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("storage_boxes", sa.Column("id", sa.Integer, primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("name_key", sa.String(320), nullable=False, unique=True), sa.Column("description", sa.Text, nullable=False, server_default=""), sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("box_contents", sa.Column("id", sa.Integer, primary_key=True), sa.Column("box_id", sa.Integer, sa.ForeignKey("storage_boxes.id", ondelete="CASCADE"), nullable=False, index=True), sa.Column("item_id", sa.Integer, sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True), sa.Column("present_quantity", sa.Integer, nullable=False, server_default="0"), sa.UniqueConstraint("box_id", "item_id", name="uq_box_contents_box_item"), sa.CheckConstraint("present_quantity >= 0", name="ck_box_contents_present_nonnegative"))
    op.create_table("loan_groups", sa.Column("id", sa.Integer, primary_key=True), sa.Column("box_id", sa.Integer, sa.ForeignKey("storage_boxes.id", ondelete="SET NULL"), index=True), sa.Column("box_name_snapshot", sa.String(160), nullable=False), sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("box_events", sa.Column("id", sa.Integer, primary_key=True), sa.Column("item_id", sa.Integer, sa.ForeignKey("items.id", ondelete="SET NULL"), index=True), sa.Column("item_name_snapshot", sa.String(160), nullable=False), sa.Column("quantity", sa.Integer, nullable=False), sa.Column("source_box_id", sa.Integer, sa.ForeignKey("storage_boxes.id", ondelete="SET NULL")), sa.Column("destination_box_id", sa.Integer, sa.ForeignKey("storage_boxes.id", ondelete="SET NULL")), sa.Column("source_name_snapshot", sa.String(160), nullable=False), sa.Column("destination_name_snapshot", sa.String(160), nullable=False), sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    with op.batch_alter_table("borrowings") as batch:
        batch.add_column(sa.Column("box_id", sa.Integer))
        batch.add_column(sa.Column("box_name_snapshot", sa.String(160), nullable=False, server_default=""))
        batch.add_column(sa.Column("group_id", sa.Integer))
        batch.create_foreign_key("fk_borrowings_box_id", "storage_boxes", ["box_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_borrowings_group_id", "loan_groups", ["group_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_borrowings_box_id", ["box_id"])
        batch.create_index("ix_borrowings_group_id", ["group_id"])
    with op.batch_alter_table("stock_movements") as batch:
        batch.add_column(sa.Column("box_id", sa.Integer))
        batch.add_column(sa.Column("box_name_snapshot", sa.String(160), nullable=False, server_default=""))
        batch.create_foreign_key("fk_stock_movements_box_id", "storage_boxes", ["box_id"], ["id"], ondelete="SET NULL")


def downgrade():
    connection = op.get_bind()
    for table in ("storage_boxes", "box_contents", "loan_groups", "box_events"):
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("Retour impossible : des boîtes ou leur historique existent.")
    for table in ("borrowings", "stock_movements"):
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE box_id IS NOT NULL OR box_name_snapshot <> ''")).scalar():
            raise RuntimeError("Retour impossible : des provenances existent.")
    if connection.execute(sa.text("SELECT COUNT(*) FROM borrowings WHERE group_id IS NOT NULL")).scalar():
        raise RuntimeError("Retour impossible : des sorties groupées existent.")
    with op.batch_alter_table("stock_movements") as batch:
        batch.drop_constraint("fk_stock_movements_box_id", type_="foreignkey")
        batch.drop_column("box_name_snapshot")
        batch.drop_column("box_id")
    with op.batch_alter_table("borrowings") as batch:
        batch.drop_index("ix_borrowings_group_id")
        batch.drop_index("ix_borrowings_box_id")
        batch.drop_constraint("fk_borrowings_group_id", type_="foreignkey")
        batch.drop_constraint("fk_borrowings_box_id", type_="foreignkey")
        batch.drop_column("group_id")
        batch.drop_column("box_name_snapshot")
        batch.drop_column("box_id")
    for table in ("box_events", "loan_groups", "box_contents", "storage_boxes"):
        op.drop_table(table)

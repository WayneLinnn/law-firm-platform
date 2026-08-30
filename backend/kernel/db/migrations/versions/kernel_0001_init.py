"""内核基表：记录当前装载了哪些模块

Revision ID: kernel_0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "kernel_0001"
down_revision = None
branch_labels = ("kernel",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "module_state",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="kernel",
    )


def downgrade() -> None:
    op.drop_table("module_state", schema="kernel")

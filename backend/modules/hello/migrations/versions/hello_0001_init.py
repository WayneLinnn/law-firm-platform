"""hello 模块建表

Revision ID: hello_0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "hello_0001"
down_revision = None
branch_labels = ("hello",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="hello",
    )


def downgrade() -> None:
    op.drop_table("note", schema="hello")

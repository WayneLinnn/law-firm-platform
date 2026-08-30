"""收费约定加「咨询单价」

★ 「按次咨询计费」这个模式原来**没有地方填价** —— 表单上只有一个
  "封顶价（可空）"。用户的原话是「只能填类型，不能填金额，如何算钱呢」，
  他是对的：**一个填不了钱的收费约定，等于没约定。**

★ 为什么不直接用本所费率表：大客户会单独谈价（"我们谈的是 800"），
  而费率表是按职级配的、全所一个数。谈定的价要跟着这份约定走。

可空 —— 老约定升上来是空，表示"用本所费率"，行为和以前一样。

Revision ID: f9d2c6b0a417
Revises: 44d73b6e4eaa
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'f9d2c6b0a417'
down_revision = '44d73b6e4eaa'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'engagement',
        sa.Column('consult_rate', sa.Numeric(precision=18, scale=2), nullable=True),
        schema='billing',
    )


def downgrade() -> None:
    op.drop_column('engagement', 'consult_rate', schema='billing')

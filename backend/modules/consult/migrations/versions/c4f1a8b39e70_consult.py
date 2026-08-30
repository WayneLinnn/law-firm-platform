"""consult 法律咨询

★ 这张表是 time（工时）拆掉之后剩下的那一点点。
  提成制律所不按工时给客户开账单，但**咨询确实按小时收钱** —— 只留这个。

Revision ID: c4f1a8b39e70
Revises:
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4f1a8b39e70'
down_revision = None
branch_labels = ('consult',)
depends_on = None


def upgrade() -> None:
    op.create_table(
        'consult',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        # ★ 客户必填 —— 收了钱得知道是谁的钱，而且以后利冲要查得到他
        sa.Column('client_id', sa.Integer(), nullable=False),
        # ★ 案件选填 —— 多数咨询发生在立案之前，甚至根本不会立案
        sa.Column('case_id', sa.Integer(), nullable=True),
        sa.Column('happened_on', sa.Date(), nullable=False),
        sa.Column('minutes', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=12), nullable=False,
                  server_default='office'),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('hourly_rate', sa.Numeric(precision=18, scale=2),
                  nullable=False, server_default='0'),
        # ★ 算好的钱存下来，不每次按当前费率重算 —— 费率会调，旧记录不该跟着变
        sa.Column('amount', sa.Numeric(precision=18, scale=2),
                  nullable=False, server_default='0'),
        sa.Column('free', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('free_reason', sa.String(length=120), nullable=False,
                  server_default=''),
        sa.Column('status', sa.String(length=12), nullable=False,
                  server_default='draft'),
        sa.Column('invoice_no', sa.String(length=40), nullable=False,
                  server_default=''),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_consult')),
        schema='consult',
    )
    op.create_index('ix_consult_tenant_client', 'consult',
                    ['tenant_id', 'client_id'], schema='consult')
    op.create_index('ix_consult_tenant_date', 'consult',
                    ['tenant_id', 'happened_on'], schema='consult')


def downgrade() -> None:
    op.drop_index('ix_consult_tenant_date', table_name='consult', schema='consult')
    op.drop_index('ix_consult_tenant_client', table_name='consult', schema='consult')
    op.drop_table('consult', schema='consult')

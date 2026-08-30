"""lawyer license (执业资格)

★ 律所独有的一组字段：一个人能不能办案，不取决于所里给他什么角色，
  取决于司法行政机关发没发证、证还有没有效。

全部可空 / 有默认值 —— 老库升上来是「未登记」，不会把任何人挡在门外。
**加一列就让所有人办不了案，是最糟糕的迁移。**

Revision ID: a7c31e9d4b02
Revises: 59489c7f5d40
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c31e9d4b02'
down_revision = '59489c7f5d40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user',
                  sa.Column('license_no', sa.String(length=64),
                            nullable=False, server_default=''),
                  schema='identity')
    op.add_column('user',
                  sa.Column('license_status', sa.String(length=20),
                            nullable=False, server_default=''),
                  schema='identity')
    op.add_column('user',
                  sa.Column('licensed_on', sa.Date(), nullable=True),
                  schema='identity')
    op.add_column('user',
                  sa.Column('license_valid_to', sa.Date(), nullable=True),
                  schema='identity')
    op.add_column('user',
                  sa.Column('license_firm', sa.String(length=200),
                            nullable=False, server_default=''),
                  schema='identity')
    # 一个执业证号只能挂一个人 —— 两个人挂同一个号多半是录错了，
    # 而错的那个会一路错到委托书上。
    op.create_index('ix_user_license_no', 'user',
                    ['tenant_id', 'license_no'], schema='identity')


def downgrade() -> None:
    op.drop_index('ix_user_license_no', table_name='user', schema='identity')
    for col in ('license_firm', 'license_valid_to', 'licensed_on',
                'license_status', 'license_no'):
        op.drop_column('user', col, schema='identity')

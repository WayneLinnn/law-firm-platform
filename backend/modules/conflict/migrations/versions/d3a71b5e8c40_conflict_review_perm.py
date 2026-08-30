"""给已建租户补发 conflict.review

★ 利冲处置从一档拆成两档：

      blocked（对方就是我方客户）→ conflict.decide  管委会
      review （关联方、旧客户）  → conflict.review   风控 / 主任

  原因：**管委会在中国律所不是天天在线的审批岗，是开会的机构。**
  把"关联方在别的案子里出现过"也推给它，它待办里就堆满本该秒过的东西，
  然后养成"全点放行"的习惯 —— 这道闸就废了。

★ **新权限点必须靠迁移发到老库。**
  `ensure_default_roles` 对已存在的角色整个跳过（那个设计是对的：
  新模块上线该给谁由律所决定），所以老库不会自己长出这个权限 ——
  结果是团队负责人点处置报 403，而且**不报错、只是按钮不出现**。

  这已经是第三次踩「新库对、老库错」：
    ① Step 14/15 ensure_default_rules 不回填新字段
    ② time.* 改名成 consult.*，库里没跟着改
    ③ 就是这次

Revision ID: d3a71b5e8c40
Revises: 8763639346a2
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa


revision = 'd3a71b5e8c40'
down_revision = '8763639346a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ① 有 conflict.decide 的（管委会）一并给 review —— decide 是 review 的超集
    conn.execute(sa.text("""
        insert into identity.role_permission (role_id, permission)
        select rp.role_id, 'conflict.review'
          from identity.role_permission rp
         where rp.permission = 'conflict.decide'
           and not exists (
               select 1 from identity.role_permission x
                where x.role_id = rp.role_id
                  and x.permission = 'conflict.review')
    """))

    # ② 团队负责人（主任 / 风控那一层）拿到 review
    conn.execute(sa.text("""
        insert into identity.role_permission (role_id, permission)
        select r.id, 'conflict.review'
          from identity.role r
         where r.code = 'team_leader'
           and not exists (
               select 1 from identity.role_permission x
                where x.role_id = r.id
                  and x.permission = 'conflict.review')
    """))


def downgrade() -> None:
    op.get_bind().execute(sa.text(
        "delete from identity.role_permission where permission = 'conflict.review'"))

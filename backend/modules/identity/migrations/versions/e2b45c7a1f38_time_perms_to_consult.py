"""把角色里的 time.* 权限点换成 consult.*

★ 工时模块拆掉了（见 DEVELOPMENT_PLAN Step 16），但**权限点是存在库里的** ——
  已经建好的租户，角色上挂的还是 `time.log` / `time.view` 这些死代码，
  而新的 `consult.*` 谁都没有。

  结果不是报错，是**功能静默消失**：菜单不显示、接口 403，
  没有任何东西提示你"这里少了点什么"。

★ `ensure_default_roles` 帮不上忙 —— 它对**已存在的角色整个跳过**，
  刻意不补新权限（新模块上线时，该给谁由律所自己决定，不是代码替他定）。
  那个设计是对的，但**改名不是新增**：
  老代码没了、新代码没发，中间掉下去的就是这次这个洞。

  所以改名要走迁移：**只跑一次、有版本、能回滚。**

Revision ID: e2b45c7a1f38
Revises: a7c31e9d4b02
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'e2b45c7a1f38'
down_revision = 'a7c31e9d4b02'
branch_labels = None
depends_on = None


#: 旧代码 → 新代码。
#:
#: ★ 只映射**语义真的对得上**的那几个。
#:   剩下的（log_others / approve / manage_policy）在咨询里根本不存在 ——
#:   咨询没有代录、没有审批、没有可配的取整口径，
#:   硬找一个新代码塞进去，等于凭空给人发了个他从来没有过的权限。
RENAME = {
    "time.log": "consult.log",
    "time.view": "consult.view",
    "time.view_all": "consult.view_all",
    "time.manage_policy": "consult.manage_rate",
}

#: 直接删掉、不换的。见上面的理由。
DROP = ("time.log_others", "time.approve")


def upgrade() -> None:
    conn = op.get_bind()

    for old, new in RENAME.items():
        # ★ 先删掉"已经有新代码"的那些行再改名 —— 否则唯一约束会撞。
        #   （同一个角色可能因为别的路径已经拿到了新代码）
        conn.execute(sa.text("""
            delete from identity.role_permission rp
             where rp.permission = :old
               and exists (select 1 from identity.role_permission x
                            where x.role_id = rp.role_id
                              and x.permission = :new)
        """), {"old": old, "new": new})
        conn.execute(sa.text("""
            update identity.role_permission
               set permission = :new
             where permission = :old
        """), {"old": old, "new": new})

    for dead in DROP:
        conn.execute(sa.text(
            "delete from identity.role_permission where permission = :p"),
            {"p": dead})


def downgrade() -> None:
    conn = op.get_bind()
    for old, new in RENAME.items():
        conn.execute(sa.text("""
            update identity.role_permission
               set permission = :old
             where permission = :new
        """), {"old": old, "new": new})
    # DROP 掉的那两个还不回来 —— 谁当初有 time.approve 已经无从得知

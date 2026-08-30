"""清掉案由上那两个不存在的流程模板 key

★ 案由字典里存着 `construction_v1` / `labor_v1` —— 界面把它们当权威标签
  显示得像模像样，**而真实 key 是 `construction.dispute`，labor 干脆没有模板**。
  也就是说这一栏从头到尾是死数据：没人用它选过流程，
  但它看起来是权威的 —— 下一个配案由的人会照着填，然后奇怪为什么没反应。

  **看起来在生效、其实没接线的字段，比空字段危险。**

★ 代码那边（`DEFAULT_CAUSES`）在文档审计时已经修过了，
  **但 `ensure_default_causes` 对已存在的案由整个跳过** —— 老库里的坏值
  一直留着。这已经是第三次踩「新库对、老库错」：

    ① Step 14/15 `ensure_default_rules` 不回填新字段
    ② time.* 权限点改名成 consult.*，库里没跟着改
    ③ 就是这次

  三次都**不报错**，只是行为不对。所以改名/改值一律走迁移。

Revision ID: b8e4f1c92a05
Revises: d9ade3c43bbf
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa


revision = 'b8e4f1c92a05'
down_revision = 'd9ade3c43bbf'
branch_labels = None
depends_on = None


#: 坏 key → 对的 key。空串表示"这个案由本来就没有模板"。
FIX = {
    "construction_v1": "construction.dispute",
    "labor_v1": "",
}


def upgrade() -> None:
    conn = op.get_bind()
    for bad, good in FIX.items():
        conn.execute(sa.text("""
            update "case".cause_of_action
               set workflow_template = :good
             where workflow_template = :bad
        """), {"bad": bad, "good": good})


def downgrade() -> None:
    # 不还原 —— 还原就是把坏数据放回去
    pass

"""billing 模块的对外唯一出口。

★ `check_contingency` 特意导出来给别的模块用：将来 case 模块在建案时
  就该提示"这个案由不能风险代理"，而不是等到签合同才发现。
"""
from modules.billing.rules import CONTINGENCY_MAX_RATE
from modules.billing.service import (
    metrics,
    revenue_by_lead,
    case_revenue,
    archive_inventory,
    archive_blockers,
    add_line,
    bill_contingency,
    bill_fixed,
    case_billing,
    check_contingency,
    create_engagement,
    create_invoice,
    describe_engagement,
    describe_invoice,
    describe_line,
    describe_disbursement,
    describe_payment,
    engagement_of,
    invoices_of_case,
    issue,
    lines_of,
    list_rates,
    money,
    payments_of,
    pending_disbursements,
    pull_disbursements,
    pull_consult,
    record_disbursement,
    rate_for,
    receivables,
    record_payment,
    remove_line,
    reverse_payment,
    set_rate,
    split_tax,
    void_invoice,
)

__all__ = [
    # 看板指标（Step 21）——各模块自报，analytics 只汇总
    "metrics", "revenue_by_lead", "case_revenue",
    # 归档：卷宗目录 / 归档前的阻断检查（archive 模块来问）
    "archive_inventory", "archive_blockers",
    # 费率
    "set_rate", "list_rates", "rate_for",
    # 收费约定 —— ★ 风险代理的合法性在这一层硬拦
    "create_engagement", "engagement_of", "check_contingency",
    "CONTINGENCY_MAX_RATE",
    # 账单
    "create_invoice", "add_line", "remove_line", "lines_of",
    "pull_consult", "bill_fixed", "bill_contingency", "issue", "void_invoice",
    # 回款
    "record_payment", "reverse_payment", "payments_of",
    # 代垫费用 —— ★ approval（Step 18）付完款回写这里
    "record_disbursement", "pending_disbursements", "pull_disbursements",
    # 查询
    "invoices_of_case", "case_billing", "receivables",
    # 算钱的工具，别处要用就走这里，别自己再写一份
    "money", "split_tax",
    # 展示
    "describe_engagement", "describe_invoice", "describe_line",
    "describe_payment", "describe_disbursement",
]

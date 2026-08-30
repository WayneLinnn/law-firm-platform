# -*- coding: utf-8 -*-
"""账单。全部落在 billing schema。

五张表，对应收钱这件事的五个阶段：

    fee_rate       本所**按什么价**卖时间 —— 按职级，规则即数据
    engagement     这个案子**怎么收** —— 委托合同里约定的那件事
    invoice        一张账单
    invoice_line   账单上的每一行 —— 客户逐行看、逐行砍
    payment        回款 —— 钱真的到账了才算收到

★ 为什么「收费约定」要单独一张表，不是账单上的几个字段：
  约定是签合同那一刻定下的，账单是几个月后开的。中间律师换了、
  费率调了、案子从一审走到了执行 —— **账单要按当初约定的算，
  不是按今天的价目表算**。而且风险代理的合法性检查必须在
  **签约时**就做，不能等开账单才发现这个案子根本不许风险代理。

★ 为什么钱一律用 Numeric 不用 float：
  0.1 + 0.2 != 0.3 这件事，放在律师费上就是账单和收款对不上一分钱，
  然后财务花半天去找。Python 侧一律 Decimal，库里一律 Numeric(18, 2)。

★ 为什么「代垫费用」要和「律师费」分开：
  不是为了整齐。诉讼费、保全费、鉴定费是**替客户垫的钱**，原价转付、
  不加价，增值税处理也不一样；更要紧的是 —— **风险代理那 30% 的上限，
  基数里不含代垫费用**。混在一起算，上限就算错了，而算错的方向
  永远是超收。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "billing"


def _table_args(*extra):
    return tuple(extra) + ({"schema": SCHEMA},)


#: 收费模式。
#:
#: ★ **`fixed` 排第一是刻意的** —— 界面的默认值跟着这个顺序走。
#:   原来第一个是 `hourly`（计时收费），于是签约定的表单一打开就是计时模式，
#:   而计时模式**只有"封顶价"没有金额栏** ——
#:   用户看到的是"只能填类型，不能填金额"。
#:
#:   更糟的是工时模块拆掉之后，`hourly` **已经没有东西可以计**了：
#:   一个默认选中、填不了钱、也出不了账的模式。
#:
#: ★ `hourly` 保留但改名为「按次咨询计费」，因为**咨询确实按小时收钱** ——
#:   这是拆掉工时之后唯一还按时间算的收费方式。
FEE_MODES = ("fixed", "contingency", "hybrid", "retainer", "hourly")

FEE_MODE_LABELS = {
    "fixed": "固定收费（包干）",
    "contingency": "风险代理",
    "hybrid": "混合（基础费＋风险）",
    "retainer": "常年顾问费",
    "hourly": "按次咨询计费",
}

#: 账单状态。
#:
#: ★ issued 之后**不许改**。账单是给出去的东西，改一张已经发出去的账单
#:   等于两份文件在世上打架。要更正只能红冲（void）后重开。
INVOICE_STATUSES = ("draft", "issued", "partial", "paid", "void")

INVOICE_STATUS_LABELS = {
    "draft": "草稿", "issued": "已开出", "partial": "部分回款",
    "paid": "已结清", "void": "已红冲",
}

LOCKED_INVOICE_STATUSES = ("issued", "partial", "paid", "void")

#: 账单行的种类。★ fee 和 disbursement 必须分开，理由见模块头
LINE_KINDS = ("fee", "disbursement", "discount")

LINE_KIND_LABELS = {
    "fee": "律师费", "disbursement": "代垫费用", "discount": "折让",
}

#: 回款方式
PAY_METHODS = ("transfer", "cash", "check", "offset", "other")

PAY_METHOD_LABELS = {
    "transfer": "银行转账", "cash": "现金", "check": "支票",
    "offset": "抵扣", "other": "其他",
}

#: 增值税率。律所一般纳税人 6%，小规模 3%（含各期优惠税率，所以做成数据）
DEFAULT_VAT_RATE = Decimal("0.06")


class FeeRate(Base):
    """费率表：本所按什么价卖时间。

    ★ 按**职级**定价而不是按人：律所调价是整体调的（"今年高级合伙人
      提到 4000"），一个人一个价的表，调一次要改几十行，改漏一行
      就有人按去年的价开了账单。要给某个人特价，再单开一条覆盖。
    """

    __tablename__ = "fee_rate"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "position_code", "user_id", "valid_from",
                         name="uq_fee_rate_scope"),
        Index("ix_fee_rate_tenant", "tenant_id", "valid_from"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 按职级定价。和 user_id 二选一
    position_code: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    #: 给某个人的特价。填了就盖过职级价
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    #: 元 / 小时
    hourly_rate: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)

    #: ★ 费率有生效期。调价不能把历史账单一起改了 ——
    #:   账单按**当时**的价算，所以查费率永远要带一个日期
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Engagement(Base):
    """收费约定：这个案子怎么收钱。对应委托代理合同里的收费条款。"""

    __tablename__ = "engagement"
    __table_args__ = _table_args(
        Index("ix_engagement_case", "tenant_id", "case_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 收谁的钱。客户不一定等于当事人（母公司付款、法援等）
    client_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    mode: Mapped[str] = mapped_column(String(16), nullable=False)

    #: 固定收费的总价 / 顾问费的年费
    fixed_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    #: 风险代理比例，如 0.2000 = 20%
    #:
    #: ★ 上限 30%，**法定**（《律师服务收费管理办法》第十三条）。
    #:   不是本所政策，改不了，所以硬编在校验里而不是放进配置表。
    contingency_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    #: 风险代理的计费基数（标的额）。为空则按实际回款算
    contingency_base: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    #: 混合收费里的那部分基础费（打折的计时费或固定费）
    base_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    #: 封顶价。★ 客户最常提的要求就是"给我一个上限"
    cap_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    #: 这个客户谈定的**咨询单价**（元/小时）。空 = 用本所费率表。
    #:
    #: ★ 加这一列是因为「按次咨询计费」这个模式原来**没有地方填价** ——
    #:   表单上只有一个"封顶价（可空）"。用户的原话是
    #:   「只能填类型，不能填金额，如何算钱呢」，他是对的：
    #:   **一个填不了钱的收费约定，等于没约定。**
    #:
    #: ★ 为什么不直接用本所费率表：大客户会单独谈价（"我们谈的是 800"），
    #:   而费率表是按职级配的、全所一个数。谈定的价要跟着这份约定走。
    consult_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )

    #: ★ 报价含不含税，必须**明确写死**。
    #:   中国律所的收费纠纷里，"这个价含不含税"排前三 ——
    #:   口头谈的时候谁也没说，开票的时候差 6%，两边都觉得对方赖账。
    tax_included: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=DEFAULT_VAT_RATE, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)

    #: 合同编号与签署日
    contract_no: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    signed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    #: 签这个约定时，系统给出的合法性提示（风险代理禁区、上限等）
    #: ★ 存下来是为了**事后能证明当时提示过** —— 收费违规被查时，
    #:   "系统提示过、律师确认过"和"没人提过"是两种处境
    legality_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confirmed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Invoice(Base):
    """一张账单。"""

    __tablename__ = "invoice"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "number", name="uq_invoice_number"),
        Index("ix_invoice_case", "tenant_id", "case_id"),
        Index("ix_invoice_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 账单号，全所唯一。开出去之后就是对外凭证
    number: Mapped[str] = mapped_column(String(64), nullable=False)

    case_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    client_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    engagement_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    #: 账单覆盖的工作期间
    period_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    issued_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    #: ---- 金额。都是**算出来存下来**的，不是每次查询现算 ----
    #:
    #: ★ 存下来的理由和 time.billed_minutes 一样：账单发出去那一刻的数字
    #:   必须钉死。费率改了、咨询被作废了、税率变了，都不能让一张
    #:   已经在客户手里的账单跟着变。
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    disbursement_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    #: 不含税合计
    net_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    #: 含税合计 —— 客户实际要付的数
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    #: 已收
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )

    vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=DEFAULT_VAT_RATE, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)

    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)
    #: 红冲原因 / 作废原因
    void_reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 红冲时指向被冲掉的那张
    voids_invoice_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    issued_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )


class InvoiceLine(Base):
    """账单上的一行。★ 客户是**逐行**看、逐行砍的。"""

    __tablename__ = "invoice_line"
    __table_args__ = _table_args(
        Index("ix_invoice_line_invoice", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_id: Mapped[int] = mapped_column(Integer, nullable=False)

    kind: Mapped[str] = mapped_column(String(16), default="fee", nullable=False)
    #: 这行是给客户看的说明。咨询行直接用咨询摘要 ——
    #: ★ 那句话本来就是写给客户读的，到这里不用再编一遍
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: 计时行才有：谁的、几小时、什么价
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: ★ 费率快照。费率表以后改了，这张账单上的数字不能跟着变
    rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    #: 不含税金额
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    #: 来源记录 id（逗号分隔，带前缀：C=咨询 D=代垫）。
    #: ★ 客户质疑某一行时要能翻回原始记录
    source_entry_ids: Mapped[str] = mapped_column(Text, default="", nullable=False)

    worked_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Disbursement(Base):
    """已经垫出去、**还没开进账单**的代垫费用。

    ★ 为什么要有这张表：Step 17 只能在开账单时**手工敲**一行代垫费用。
      问题是垫钱和开账单差着好几个月 —— 律师三月替客户垫了鉴定费，
      六月开账单时全靠翻财务凭证回忆。翻漏了就是律所自己贴钱。

    ★ 由 approval（Step 18）在请款单走完流程时写进来：钱一付出去，
      这笔代垫就挂在案子上等着开票，不依赖任何人记得。
    """

    __tablename__ = "disbursement"
    __table_args__ = _table_args(
        Index("ix_disbursement_case", "tenant_id", "case_id", "billed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    #: 给客户看的说明 —— 会原样进账单行
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    spent_on: Mapped[date] = mapped_column(Date, nullable=False)

    #: 来源单据（approval 的 ticket id）。★ 存裸 id 不建外键 ——
    #: 跨 schema 外键会把两个模块焊死
    ticket_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: 谁垫的钱
    paid_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    #: 已经开进哪张账单了。★ 和咨询的 billed 一个道理：开过的不能再开一次
    billed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    invoice_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Payment(Base):
    """回款。★ 钱到账了才算收到 —— 开了账单不等于收到钱。"""

    __tablename__ = "payment"
    __table_args__ = _table_args(
        Index("ix_payment_invoice", "tenant_id", "invoice_id"),
        Index("ix_payment_date", "tenant_id", "paid_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_id: Mapped[int] = mapped_column(Integer, nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(16), default="transfer", nullable=False)
    #: 银行流水号 / 支票号 —— 对账时唯一能核的东西
    reference: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    #: 冲回（退款、记错了）。★ 同样不删除，留痕
    reversed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reverse_reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    recorded_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

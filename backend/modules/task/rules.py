# -*- coding: utf-8 -*-
"""内置时限规则。

**这些只是"随平台带的起始规则"，不是全集。** 律所可以改、可以停用、可以加。

★ 三条约束，写在最前面（手册 Step 15 的硬要求）：

  1. **规则是数据不是代码。** 这里只是 `ensure_default_rules()` 的种子，
     落库之后律所自己维护。地方差异、新司法解释、内部提前量都靠改数据。

  2. **覆盖不了的必须说出来。** 每条规则带一个 `caveat`，写清它盖不住什么。
     时限有中止、中断、跨程序阶段、地方口径差异，一张表撑不住全部 ——
     撑不住的地方要**明确标出来让人确认**，绝不能默默算一个数字出来。

  3. **产品上是「提示」不是「承诺」。** 算错时限有法律责任。
     界面上一律写"请核对"，没有一处写"已确认无误"。

起算日期字段名（anchor_field）和 workflow 抽出来的要素字段对齐：

    payable_on    应付款日期        completed_on  竣工日期
    handover_on   交付日期          settled_on    结算日期
    served_on     收到裁判文书日期    known_on      知道权利受损害之日
"""
from __future__ import annotations

from typing import Any, Dict, List

#: 我方角色。空字符串 = 不分角色都适用
CONTRACTOR = "contractor"
EMPLOYER = "employer"


BUILTIN: List[Dict[str, Any]] = [
    # ================================================================ 建设工程
    {
        "code": "construction.priority",
        "name": "工程价款优先受偿权行使期限",
        "basis": "《建工解释一》第四十一条",
        "cause_code": "construction",
        "role": CONTRACTOR,
        "anchor_field": "payable_on",
        "months": 6,
        "fatal": True,
        "suspendable": False,
        "warn_days": 60,
        "caveat": "起算点是「发包人应当给付建设工程价款之日」，"
                  "合同没约定付款时间时实务口径不一（有按竣工日、有按交付日、"
                  "有按结算完成日）——**起算日必须由律师确认**。",
        "note": "★ 除斥期间，不中断不中止。过了就永久丧失优先受偿权，只剩普通债权。",
    },
    {
        "code": "construction.limitation",
        "name": "工程款请求权诉讼时效",
        "basis": "《民法典》第一百八十八条",
        "cause_code": "construction",
        "role": CONTRACTOR,
        "anchor_field": "payable_on",
        "months": 36,
        "fatal": False,
        "suspendable": True,
        # ★ 188 条 2 款：自权利受损害之日起 20 年，法院不予保护，
        #   且**不适用中断中止**。不设这个上限，一个每年发一次催款函的
        #   案子会被系统一路显示「正常」到第 21 年。
        "outer_years": 20,
        "warn_days": 90,
        "caveat": "可中断：催款函、对账单、对方部分履行都能重新起算；中止另算（见「登记中止」）—— "
                  "但要留得住证据。中断了请在本条上登记，届满日会重算。"
                  "★ 无论中断多少次都顶不过自权利受损害之日起 20 年"
                  "（188 条 2 款），那 20 年不适用中断中止。",
        "note": "自知道或应当知道权利受损害及义务人之日起三年。",
    },
    {
        "code": "construction.settlement_silence",
        "name": "发包人对竣工结算文件的答复期限",
        "basis": "《建工解释一》第二十一条",
        "cause_code": "construction",
        "role": CONTRACTOR,
        "anchor_field": "settled_on",
        "days": 28,
        "fatal": False,
        "suspendable": False,
        "warn_days": 7,
        "caveat": "**只有合同明确约定了「逾期不答复视为认可」才适用**，"
                  "且约定的期限可能不是 28 天 —— 请按合同改这条规则的天数，"
                  "没有这类约定就把本条停用。",
        "note": "约定期限内不予答复的，可主张按承包人提交的结算文件结算。",
    },
    {
        "code": "construction.quality",
        "name": "地基基础与主体结构质量责任",
        "basis": "《建设工程质量管理条例》第四十条",
        "cause_code": "construction",
        "role": "",
        "anchor_field": "completed_on",
        "months": 0,           # 没有固定期间，只做长期提示
        "fatal": False,
        "suspendable": False,
        "warn_days": 0,
        "caveat": "期限是「设计文件规定的合理使用年限」，**因工程而异**，"
                  "平台算不出具体届满日 —— 请查设计文件后手工登记。",
        "note": "在合理使用年限内都要担责，不因竣工验收合格而免除。",
    },

    # ================================================================ 通用诉讼
    {
        "code": "civil.appeal_judgment",
        "name": "一审判决上诉期",
        "basis": "《民事诉讼法》第一百七十一条",
        "cause_code": "",
        "role": "",
        "anchor_field": "served_on",
        "days": 15,
        "fatal": True,
        "suspendable": False,
        "warn_days": 5,
        "caveat": "**从收到判决书之次日起算**，不是判决日期。"
                  "涉外当事人在国外的为 30 日 —— 那种情形请另建规则。",
        "note": "★ 过了就丧失上诉权，判决生效。",
    },
    {
        "code": "civil.appeal_ruling",
        "name": "一审裁定上诉期",
        "basis": "《民事诉讼法》第一百七十二条",
        "cause_code": "",
        "role": "",
        "anchor_field": "served_on",
        "days": 10,
        "fatal": True,
        "suspendable": False,
        "warn_days": 3,
        "caveat": "裁定是 10 日、判决是 15 日，**别搞混**。"
                  "只有不予受理、管辖权异议、驳回起诉三类裁定可上诉。",
        "note": "★ 除斥期间。",
    },
    {
        "code": "civil.limitation",
        "name": "普通诉讼时效",
        "basis": "《民法典》第一百八十八条",
        "cause_code": "",
        "role": "",
        "anchor_field": "known_on",
        "months": 36,
        "fatal": False,
        "suspendable": True,
        # ★ 188 条 2 款：自权利受损害之日起 20 年，法院不予保护，
        #   且**不适用中断中止**。不设这个上限，一个每年发一次催款函的
        #   案子会被系统一路显示「正常」到第 21 年。
        "outer_years": 20,
        "warn_days": 90,
        "caveat": "自权利人知道或应当知道权利受损害及义务人之日起算 —— "
                  "**这个日期常有争议，必须由律师确认**。"
                  "另有最长 20 年保护期，本条不覆盖。",
        "note": "可中止、可中断。",
    },
    {
        "code": "civil.enforce",
        "name": "申请强制执行期限",
        "basis": "《民事诉讼法》第二百五十条",
        "cause_code": "",
        "role": "",
        "anchor_field": "effective_on",
        "months": 24,
        "fatal": False,
        "suspendable": True,
        "warn_days": 60,
        "caveat": "自法律文书规定的履行期间**最后一日**起算；"
                  "分期履行的从最后一期起算 —— 分期的情形请手工登记起算日。",
        "note": "适用诉讼时效中止、中断的规定。",
    },

    # ================================================================ 劳动争议
    # ★ 库里目前**一部劳动法条都没有**（见 Step 8.5），所以这条只算时限，
    #   检索那边给不出依据。留着是因为时限计算本身不依赖语料。
    {
        "code": "labor.arbitration",
        "name": "劳动争议仲裁时效",
        "basis": "《劳动争议调解仲裁法》第二十七条",
        "cause_code": "labor",
        "role": "",
        "anchor_field": "known_on",
        "months": 12,
        "fatal": False,
        "suspendable": True,
        "warn_days": 30,
        "caveat": "劳动关系存续期间因拖欠劳动报酬发生争议的**不受一年限制**，"
                  "但终止劳动关系的应当自终止之日起一年内提出 —— "
                  "请按具体情形确认起算日。",
        "note": "自当事人知道或者应当知道其权利被侵害之日起计算。可中断、可中止。",
    },
    {
        "code": "labor.sue_after_award",
        "name": "对仲裁裁决不服的起诉期",
        "basis": "《劳动争议调解仲裁法》第五十条",
        "cause_code": "labor",
        "role": "",
        "anchor_field": "served_on",
        "days": 15,
        "fatal": True,
        "suspendable": False,
        "warn_days": 5,
        "caveat": "自收到仲裁裁决书之日起 15 日。**终局裁决**的劳动者一方"
                  "可起诉、用人单位只能申请撤销（30 日）—— 情形不同请另建规则。",
        "note": "★ 过了裁决即生效。",
    },
]


def by_code(code: str) -> Dict[str, Any]:
    for r in BUILTIN:
        if r["code"] == code:
            return r
    return {}

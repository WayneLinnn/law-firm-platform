# -*- coding: utf-8 -*-
"""建设工程施工合同纠纷 —— 业务规则。

**这个文件是 Step 10 的全部价值所在，也是"流程驱动检索"这句话的兑现处。**

律师自己搜是这样的：在搜索框敲"工程款"，拿回一堆同主题的东西，
然后凭经验判断该看哪些、还漏了什么。漏没漏，只有他自己知道。

流程驱动是这样的：先把案情拆成**要素**（有没有验收、结没结算、
是不是借资质、欠多少、什么时候该付的），律师确认一遍；
然后由**规则**决定这个案子该查哪几个争议焦点、每个焦点该用什么检索式。
律师不用想「我该搜什么」，因为该搜什么是案子的要素决定的。

所以这里的两张表才是核心资产：

    DEADLINE_RULES   时限 —— 律师最容易漏、漏了最要命的东西
    ISSUE_RULES      争议焦点 —— 决定"这个案子要查哪几件事"

★ 时限规则**只在这里定义一次**（手册 C4：单一事实来源）。
  以后待办、日历、提醒都从这里取，绝不允许第二处再抄一份日期计算 ——
  两处算法一旦不一致，律师会信错的那个。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

# ================================================================ 我方角色

#: 承包人（施工方）视角 —— 追工程款、主张优先受偿权
CONTRACTOR = "contractor"
#: 发包人（建设单位）视角 —— 抗辩质量、主张工期违约
EMPLOYER = "employer"

ROLE_LABELS = {CONTRACTOR: "承包人（施工方）", EMPLOYER: "发包人（建设单位）"}


# ================================================================ 日期

_DATE_RE = re.compile(r"(\d{4})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})")


def parse_date(text: Any) -> Optional[date]:
    """从"2023年5月18日" / "2023-05-18" 这类写法里抠出日期。

    抠不出来就返回 None —— **绝不猜**。时限算错比算不出来危险得多：
    算不出来律师会自己去看，算错了他会信。
    """
    if isinstance(text, date):
        return text
    if not text:
        return None
    m = _DATE_RE.search(str(text))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _plus_months(d: date, months: int) -> date:
    """加月份。按"对应日的前一日"处理，月末不足则取当月最后一天。"""
    y, m = d.year, d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    day = d.day
    while day > 0:
        try:
            return date(y, m, day)
        except ValueError:
            day -= 1
    return d


# ================================================================ 时限

#: 建工案子里会要命的几个期间。
#:
#: ★ 每条都写清 **起算点** 和 **依据** —— 律师看到预警要能立刻核对，
#:   而不是被一个算不清来路的红字吓一跳。
#:
#: 字段：
#:   key       内部标识
#:   name      人话名字
#:   basis     法律依据（写进预警里，律师直接能引）
#:   months    期间长度（月）
#:   anchor    从要素里的哪个日期起算
#:   fatal     True = 过期即丧失实体权利（除斥期间），不是"可能被抗辩"
#:   note      起算点的实务说明
DEADLINE_RULES: List[Dict[str, Any]] = [
    {
        "key": "priority",
        "name": "工程价款优先受偿权行使期限",
        "basis": "《建工解释一》第四十一条",
        "months": 6,
        "anchor": "payable_on",
        "fatal": True,
        "roles": (CONTRACTOR,),
        "note": "自发包人应当给付建设工程价款之日起算。**除斥期间，不中断不中止** ——"
                "过了就永久丧失优先受偿权，只剩普通债权。",
    },
    {
        "key": "limitation",
        "name": "工程款请求权诉讼时效",
        "basis": "《民法典》第一百八十八条",
        "months": 36,
        "anchor": "payable_on",
        "fatal": False,
        "roles": (CONTRACTOR,),
        "note": "自知道或应当知道权利受损害及义务人之日起三年。可中断 ——"
                "催款函、对账单都能重新起算，但要留证据。",
    },
    {
        "key": "quality_claim",
        "name": "地基基础与主体结构质量责任",
        "basis": "《建设工程质量管理条例》第四十条",
        "months": 0,       # 合理使用年限，没有固定月数
        "anchor": "completed_on",
        "fatal": False,
        "roles": (CONTRACTOR, EMPLOYER),
        "note": "地基基础与主体结构在**设计文件规定的合理使用年限内**都要担责，"
                "不因竣工验收合格而免除 —— 这一条没有「过期」一说。",
    },
]


def compute_deadlines(elements: Dict[str, Any], role: str,
                      today: Optional[date] = None) -> List[Dict[str, Any]]:
    """按要素算出时限清单。**纯本地计算，0 成本、0 延迟。**

    这一步是整条流程里性价比最高的：不花一分钱，却能挡住最贵的事故。
    优先受偿权是除斥期间 —— 律师接手一个案子最先该问的就是"还剩几天"，
    而这恰恰是最容易在一堆材料里被忽略的。
    """
    today = today or date.today()
    out: List[Dict[str, Any]] = []

    for rule in DEADLINE_RULES:
        if role not in rule["roles"]:
            continue
        anchor = parse_date(elements.get(rule["anchor"]))
        item: Dict[str, Any] = {
            "key": rule["key"],
            "name": rule["name"],
            "basis": rule["basis"],
            "fatal": rule["fatal"],
            "note": rule["note"],
            "anchor_field": rule["anchor"],
            "anchor_on": anchor.isoformat() if anchor else "",
        }
        if anchor is None:
            # ★ 算不出来要**明说**，不能静默跳过 —— 静默 = 律师以为没问题
            item.update({
                "status": "unknown",
                "deadline_on": "",
                "days_left": None,
                "message": "缺「{0}」，无法计算。这一步必须补齐 —— {1}".format(
                    _FIELD_LABELS.get(rule["anchor"], rule["anchor"]), rule["name"]),
            })
            out.append(item)
            continue

        if not rule["months"]:
            item.update({"status": "info", "deadline_on": "", "days_left": None,
                         "message": rule["note"]})
            out.append(item)
            continue

        due = _plus_months(anchor, rule["months"])
        left = (due - today).days
        if left < 0:
            status = "expired"
            msg = "已于 {0} 届满，超期 {1} 天。".format(due.isoformat(), -left)
            if rule["fatal"]:
                msg += "**优先受偿权已丧失**，只能按普通债权主张。"
        elif left <= 30:
            status = "urgent"
            msg = "{0} 届满，只剩 {1} 天。".format(due.isoformat(), left)
        else:
            status = "ok"
            msg = "{0} 届满，还有 {1} 天。".format(due.isoformat(), left)

        item.update({"status": status, "deadline_on": due.isoformat(),
                     "days_left": left, "message": msg})
        out.append(item)

    # 要命的排前面：已过期 > 快到期 > 算不出来 > 其余
    order = {"expired": 0, "urgent": 1, "unknown": 2, "ok": 3, "info": 4}
    out.sort(key=lambda x: (order.get(x["status"], 9), not x["fatal"]))
    return out


_FIELD_LABELS = {
    "payable_on": "应付款日期",
    "completed_on": "竣工日期",
    "handover_on": "交付日期",
    "settled_on": "结算日期",
}


# ================================================================ 争议焦点

#: 这个案子该查哪几件事，由要素决定 —— 不由律师「想到哪搜到哪」决定。
#:
#: 字段：
#:   key       内部标识
#:   title     争议焦点（写进意见书的小标题）
#:   when      判定函数：这个案子要不要查这一项
#:   queries   检索式。★ 这是"流程驱动"的兑现处 ——
#:             由要素拼出来的检索式，比律师在框里敲"工程款"精确得多
#:   why       为什么要查它。给律师看，也写进意见书
ISSUE_RULES: List[Dict[str, Any]] = [
    {
        "key": "validity",
        "title": "施工合同效力",
        "when": lambda e, r: bool(e.get("borrowed_qualification")
                                  or e.get("no_qualification")
                                  or e.get("subcontracted")),
        "queries": [
            "借用资质 挂靠 施工合同 效力 无效",
            "没有资质 承揽工程 合同无效 折价补偿",
            "违法分包 转包 合同效力",
        ],
        "why": "存在借资质／无资质／转包情形。合同无效不等于拿不到钱 ——"
               "工程验收合格的可参照合同约定折价补偿，但影响利息与违约金主张。",
    },
    {
        "key": "black_white",
        "title": "黑白合同：以哪份结算",
        "when": lambda e, r: bool(e.get("multiple_contracts") or e.get("tendered")),
        "queries": [
            "中标合同 另行签订 背离实质性内容 结算依据",
            "黑白合同 备案合同 实际履行 认定",
            "招标投标 必须招标项目 中标无效",
        ],
        "why": "同一工程存在数份合同或经过招投标。结算依据认错，整个标的额都会错。",
    },
    {
        "key": "acceptance",
        "title": "未经验收擅自使用的后果",
        "when": lambda e, r: bool(e.get("used_without_acceptance")),
        "queries": [
            "建设工程 未经竣工验收 发包人擅自使用 质量 主张权利",
            "擅自使用 视为认可质量 拒付工程款",
            "地基基础 主体结构 合理使用寿命 质量责任",
        ],
        "why": "发包人未验收即使用。这一条通常直接决定质量抗辩能否成立 ——"
               "是承包人手里最硬的牌之一。",
    },
    {
        "key": "settlement",
        "title": "工程价款如何确定",
        "when": lambda e, r: True,       # 追款案子绕不开
        "queries": [
            "竣工结算文件 逾期不答复 视为认可",
            "工程造价 司法鉴定 固定价 能否鉴定",
            "工程量 争议 签证 计价",
        ],
        "why": "工程款数额是本案标的。结算方式（约定／审核期限／鉴定）决定举证方向。",
    },
    {
        "key": "priority",
        "title": "工程价款优先受偿权",
        "when": lambda e, r: r == CONTRACTOR and bool(e.get("unpaid_amount")),
        "queries": [
            "工程价款优先受偿权 行使期限 六个月 起算",
            "优先受偿权 抵押权 顺位 优先",
            "放弃优先受偿权 约定 效力 建筑工人",
        ],
        "why": "有欠付且我方为承包人。优先受偿权优于抵押权，是能否实际收到钱的关键；"
               "但它是**除斥期间**，过期即失权。",
    },
    {
        "key": "interest",
        "title": "利息与违约金",
        "when": lambda e, r: r == CONTRACTOR and bool(e.get("unpaid_amount")),
        "queries": [
            "欠付工程款 利息 计付标准 同期贷款市场报价利率",
            "工程款 利息 起算 交付之日 竣工之日",
            "违约金 过分高于损失 调减",
        ],
        "why": "利息标准与起算点常被忽略，金额不小；对方也常反过来主张违约金过高。",
    },
    {
        "key": "actual_builder",
        "title": "实际施工人能否直接起诉发包人",
        "when": lambda e, r: r == CONTRACTOR and bool(e.get("actual_builder")),
        "queries": [
            "实际施工人 突破合同相对性 直接起诉发包人 欠付范围",
            "实际施工人 转包 违法分包 权利保护",
        ],
        "why": "我方是实际施工人。能否直接告发包人，决定被告怎么列、能否拿到钱。",
    },
    {
        "key": "delay",
        "title": "工期延误责任",
        "when": lambda e, r: bool(e.get("delayed")),
        "queries": [
            "开工日期 争议 认定 工期",
            "工期顺延 签证 索赔 逾期竣工 违约金",
        ],
        "why": "存在工期争议。发包人常以逾期竣工反诉冲抵工程款。",
    },
]


def pick_issues(elements: Dict[str, Any], role: str) -> List[Dict[str, Any]]:
    """按要素挑出该查的争议焦点。

    ★ 挑出来的顺序就是意见书的行文顺序：先谈合同效力（决定一切的前提），
      再谈钱怎么算，最后谈能不能收到。这是律师写意见书的自然顺序。
    """
    out = []
    for rule in ISSUE_RULES:
        try:
            hit = bool(rule["when"](elements, role))
        except Exception:
            hit = False
        if hit:
            out.append({
                "key": rule["key"],
                "title": rule["title"],
                "why": rule["why"],
                "queries": list(rule["queries"]),
            })
    return out


# ================================================================ 要素

#: 从案情里要抽的东西。给模型的 schema，也是律师确认清单。
ELEMENT_FIELDS: List[Dict[str, str]] = [
    {"key": "project", "label": "工程名称", "hint": "案涉工程的名称"},
    {"key": "contract_amount", "label": "合同价款", "hint": "写数字，单位元"},
    {"key": "unpaid_amount", "label": "欠付金额", "hint": "写数字，单位元"},
    {"key": "completed_on", "label": "竣工日期", "hint": "YYYY-MM-DD，没有就留空"},
    {"key": "handover_on", "label": "交付日期", "hint": "YYYY-MM-DD，没有就留空"},
    {"key": "payable_on", "label": "应付款日期", "hint":
        "发包人应当给付工程价款之日。**优先受偿权的六个月从这天起算**，最关键"},
    {"key": "settled_on", "label": "结算日期", "hint": "YYYY-MM-DD，没有就留空"},
    {"key": "used_without_acceptance", "label": "未验收即使用", "hint": "是/否"},
    {"key": "borrowed_qualification", "label": "借用资质", "hint": "是/否"},
    {"key": "no_qualification", "label": "无相应资质", "hint": "是/否"},
    {"key": "subcontracted", "label": "转包或违法分包", "hint": "是/否"},
    {"key": "multiple_contracts", "label": "存在数份合同", "hint": "是/否"},
    {"key": "tendered", "label": "经过招投标", "hint": "是/否"},
    {"key": "actual_builder", "label": "我方为实际施工人", "hint": "是/否"},
    {"key": "delayed", "label": "存在工期争议", "hint": "是/否"},
]

_BOOL_FIELDS = {f["key"] for f in ELEMENT_FIELDS if f["hint"] == "是/否"}
_TRUE = {"是", "true", "True", "1", "有", "y", "Y", True, 1}


def normalize_elements(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把模型抽出来的东西规整成固定形状。

    模型会把「是/否」写成各种花样（是、有、true、1…），也会漏字段。
    规整放在这里做一次，后面的规则就不用每条都防一遍。
    """
    out: Dict[str, Any] = {}
    for f in ELEMENT_FIELDS:
        v = raw.get(f["key"], "")
        if f["key"] in _BOOL_FIELDS:
            out[f["key"]] = v in _TRUE or (isinstance(v, str) and v.strip() in ("是", "有"))
        else:
            out[f["key"]] = "" if v is None else str(v).strip()
    return out


def elements_digest(elements: Dict[str, Any]) -> str:
    """要素摘要，拿给律师确认用。只列有值的，别拿一堆空行占地方。"""
    lines = []
    for f in ELEMENT_FIELDS:
        v = elements.get(f["key"])
        if f["key"] in _BOOL_FIELDS:
            if v:
                lines.append("· {0}：是".format(f["label"]))
        elif v:
            lines.append("· {0}：{1}".format(f["label"], v))
    return "\n".join(lines) or "（没抽到任何要素，请手工补）"

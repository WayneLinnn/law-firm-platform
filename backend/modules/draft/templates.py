# -*- coding: utf-8 -*-
"""文书模板。

**这个文件里最重要的一个判断：哪些内容绝不让模型写。**

一份起诉状里，当事人名称、住所、法院名称、诉讼标的额 —— 这些都是
**案件数据**，平台里本来就有。让模型去"生成"它们，等于让它照着上下文
猜一个名字出来。猜错一个字，立案窗口就退回；猜错金额，诉请就废了。

所以每一节标明它是怎么来的：

    fill      从案件数据直接填。**不过模型。** 缺数据就明说缺什么
    generate  要论述，交给模型 —— 但只能引用给定的依据
    manual    律师自己写（签名、日期这类）

真正需要模型的只有「事实与理由」这一类。其余全是填空。
**这不是省钱，是准确性问题。**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

#: 一节是怎么来的
FILL = "fill"
GENERATE = "generate"
MANUAL = "manual"

KIND_LABELS = {FILL: "自动填充", GENERATE: "模型起草", MANUAL: "律师手写"}


@dataclass
class Section:
    key: str
    title: str
    kind: str = GENERATE
    #: 给模型的指示（kind=generate 时用）
    brief: str = ""
    #: 从案件数据取值的字段名（kind=fill 时用），见 service._fill_value
    source: str = ""
    #: 少了这节文书就不成立
    required: bool = True
    #: 提示律师这节要注意什么
    hint: str = ""
    #: 这一节讲不讲法律道理。False 表示本来就不需要引法条
    #: （诉讼请求是请求清单、事项与范围是说明）——
    #: 对这类段落报「没有引用依据」是噪音，而噪音会让人开始忽略告警。
    needs_citation: bool = True


@dataclass
class Template:
    key: str
    label: str
    #: 一句话说明什么时候用它
    whenever: str
    sections: List[Section] = field(default_factory=list)


# ================================================================ 民事起诉状

COMPLAINT = Template(
    key="civil_complaint",
    label="民事起诉状",
    whenever="向法院提起民事诉讼时",
    sections=[
        Section("plaintiff", "原告", FILL, source="our_parties",
                hint="取自案件当事人里标了「我方」的主体"),
        Section("defendant", "被告", FILL, source="their_parties",
                hint="取自案件当事人里标了「对方」的主体"),
        Section("claims", "诉讼请求", GENERATE,
                brief="逐项列出诉讼请求，每项单独一行、编号。"
                      "金额必须与已知的标的额/欠付金额一致，**不得自行推算**；"
                      "数据缺失就写「请补充」，不要编。"
                      "最后一项写诉讼费承担。",
                needs_citation=False,   # 请求清单，不讲道理
                hint="★ 金额和请求项要律师逐条核对 —— 这一节直接决定诉请范围"),
        Section("facts", "事实与理由", GENERATE,
                brief="按时间顺序陈述事实，再分点论证请求权基础。"
                      "每个法律论断后面必须标注依据编号【S数字】。"
                      "只能用给定的依据，没有依据支撑的论断要写明「依据不足」。",
                hint="唯一真正需要模型的部分"),
        Section("court", "此致", FILL, source="court",
                hint="取自案件的法院字段"),
        Section("evidence", "证据清单", FILL, source="evidence_list", required=False,
                hint="取自案卷「证据材料」卷宗 —— 传了才有"),
        Section("signature", "具状人与日期", MANUAL, required=False,
                hint="律师签署，平台不代签"),
    ],
)

# ================================================================ 民事答辩状

DEFENSE = Template(
    key="civil_defense",
    label="民事答辩状",
    whenever="收到起诉状副本后应诉答辩时",
    sections=[
        Section("respondent", "答辩人", FILL, source="our_parties"),
        Section("claimant", "被答辩人", FILL, source="their_parties"),
        Section("summary", "答辩意见", GENERATE,
                brief="开门见山给出总的答辩意见：请求驳回全部或部分诉请，并说明理由概要。",
                hint="法官先看这一段"),
        Section("facts", "事实与理由", GENERATE,
                brief="逐项回应对方的诉讼请求与事实主张，指出其证据不足或法律适用错误之处。"
                      "每个法律论断后面标注依据编号【S数字】，只能用给定依据。",
                hint=""),
        Section("court", "此致", FILL, source="court"),
        Section("signature", "答辩人与日期", MANUAL, required=False),
    ],
)

# ================================================================ 律师函

LETTER = Template(
    key="lawyer_letter",
    label="律师函",
    whenever="诉前催告、发出正式主张时",
    sections=[
        Section("to", "致", FILL, source="their_parties",
                hint="收函方 —— 取自案件的对方当事人"),
        Section("client", "委托人", FILL, source="our_parties"),
        Section("body", "函件正文", GENERATE,
                brief="以律师事务所名义陈述事实、指出对方违约或侵权之处、"
                      "提出明确要求与履行期限，措辞正式克制。"
                      "涉及法律依据的标注【S数字】，只能用给定依据。"
                      "**不得使用威胁性表述**。",
                hint="★ 律师函会被对方拿去当证据 —— 措辞要经得起看"),
        Section("deadline", "履行期限与后果", GENERATE,
                brief="写明要求对方在多少日内履行，以及逾期将采取的合法措施。"
                      "期限如未给定就写「请补充」，不要自行设定。",
                needs_citation=False,
                hint="期限要律师定，模型不许自己编一个"),
        Section("signature", "律师事务所与日期", MANUAL, required=False),
    ],
)

# ================================================================ 法律意见书

OPINION = Template(
    key="legal_opinion",
    label="法律意见书",
    whenever="向客户出具正式法律分析时",
    sections=[
        Section("to", "致", FILL, source="client_name", required=False),
        Section("scope", "事项与范围", GENERATE,
                brief="说明本意见书针对什么事项、基于什么材料出具、有何限制。",
                needs_citation=False,   # 说明范围，不讲道理
                hint="限制条款要写清楚 —— 这是律师的责任边界"),
        Section("conclusion", "结论", GENERATE,
                brief="先给结论，一段说清。每个结论标注依据编号【S数字】。",
                hint="客户只看这一段"),
        Section("analysis", "分析", GENERATE,
                brief="按争议焦点分节论证，每节先结论后理由，"
                      "每个法律论断标注【S数字】，只能用给定依据。",
                hint=""),
        Section("advice", "下一步建议", GENERATE,
                brief="写具体动作和时间点，不要写空话。涉及时限的要写明届满日。",
                needs_citation=False,
                hint=""),
        Section("signature", "出具人与日期", MANUAL, required=False),
    ],
)


TEMPLATES: Dict[str, Template] = {
    t.key: t for t in (COMPLAINT, DEFENSE, LETTER, OPINION)
}


def get(key: str) -> Optional[Template]:
    return TEMPLATES.get(key)


def describe() -> List[Dict[str, Any]]:
    """给界面用。"""
    return [{
        "key": t.key,
        "label": t.label,
        "whenever": t.whenever,
        "sections": [{
            "key": s.key, "title": s.title, "kind": s.kind,
            "kind_label": KIND_LABELS.get(s.kind, s.kind),
            "required": s.required, "hint": s.hint,
        } for s in t.sections],
    } for t in TEMPLATES.values()]

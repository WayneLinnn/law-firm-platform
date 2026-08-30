# -*- coding: utf-8 -*-
"""业务流程模板。**这里没有一行 langgraph** —— 只声明"有哪几步、每步干什么"。

怎么把它跑成一张可中断、可恢复的图，是 adapters/workflow 的事。
换编排引擎，这个文件一个字不改。

建工流程八步：

    1 抽要素      模型 · 一次调用
    2 确认要素    ★ 律师确认，可直接改抽错的日期
    3 时限体检    本地计算 · 0 成本 —— 全流程性价比最高的一步
    4 定争议焦点  本地规则 · 0 成本
    5 逐焦点检索  ★ 流程驱动检索的兑现处 · 免费
    6 确认焦点    ★ 律师确认，可删掉不要的焦点
    7 起草意见书  模型 · 一次调用
    8 律师终审    ★ 责任落点

两次模型调用、十几次免费检索。**贵的地方不在模型，在律师的时间** ——
省下的是「我该搜什么」这个环节。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from kernel.config import settings
from kernel.container import container
from kernel.db.session import session_scope
from kernel.ports.workflow import Step, Template
from kernel.registry import registry
from modules.workflow import construction as cn

logger = logging.getLogger(__name__)

MAX_PER_QUERY = 4        # 每条检索式取几条
MAX_PER_ISSUE = 6        # 每个焦点最多留几条依据，多了律师看不完

#: 起草时给的输出配额。
#:
#: ★ 开思考时**推理 token 也占输出配额**。实测：同样的意见书提示词，
#:   max_tokens=4000 时推理把 4000 全吃光，正文返回**空字符串**；
#:   给到 8000 才正常吐出 1994 字（推理另外用掉约 3400）。
#:   适配器会为此打一条 warning，但业务侧不能指望有人去看日志。
DRAFT_MAX_TOKENS = 8000


def _cost_cny(result) -> float:
    """把适配器的记账换成人民币。适配器给的是 cost_usd —— 厂商按美元报价。"""
    usd = float((getattr(result, "usage", None) or {}).get("cost_usd", 0) or 0)
    return usd * settings.usd_cny_rate


# ================================================================ 1 抽要素


def _element_schema() -> Dict[str, Any]:
    props = {}
    for f in cn.ELEMENT_FIELDS:
        props[f["key"]] = {"type": "string", "description": f["label"] + "。" + f["hint"]}
    return {"type": "object", "properties": props, "required": []}


def _extract(state: Dict[str, Any]) -> Dict[str, Any]:
    """从案情里抽要素。机械任务，**不开思考** —— 开了纯烧推理 token。"""
    from kernel.ports.model import ChatMessage

    facts = (state.get("facts") or "").strip()
    if not facts:
        return {"elements": cn.normalize_elements({}),
                "extract_note": "案情为空，要素全部待补"}

    fields = "\n".join(
        "  {0}（{1}）：{2}".format(f["key"], f["label"], f["hint"])
        for f in cn.ELEMENT_FIELDS
    )
    prompt = (
        "从下面这段建设工程施工合同纠纷的案情里，抽取要素。\n"
        "只抽案情里**明确写了**的，没写的留空字符串 —— 不要推测、不要补全。\n\n"
        "要抽的字段：\n{0}\n\n案情：\n{1}"
    ).format(fields, facts)

    try:
        r = container.get("model").chat(
            [ChatMessage(role="system",
                         content="你是建设工程领域的律师助理，只做事实摘录，不做法律判断。"),
             ChatMessage(role="user", content=prompt)],
            json_schema=_element_schema(),
            thinking=False,          # ★ 抽取是机械活，思考模式纯属浪费
            temperature=0.0,
            max_tokens=1200,
        )
        elements = cn.normalize_elements(r.data or {})
        cost = _cost_cny(r)
    except Exception as exc:
        logger.exception("要素抽取失败")
        # ★ 抽不出来不能让整条流程死掉 —— 律师手工填一样能往下走
        return {"elements": cn.normalize_elements({}),
                "extract_note": "自动抽取失败（{0}），请手工填写".format(exc),
                "cost_cny": float(state.get("cost_cny") or 0)}

    # 先按未确认的日期算一遍时限，好在确认环节就把话说重
    preview = cn.compute_deadlines(elements, state.get("role") or cn.CONTRACTOR)
    return {
        "elements": elements,
        "deadline_preview": preview,
        "extract_note": "",
        "cost_cny": float(state.get("cost_cny") or 0) + cost,
    }


# ================================================================ 2 确认要素


def _ask_elements(state: Dict[str, Any]) -> str:
    elements = state.get("elements") or {}
    lines = ["请核对从案情里抽出的要素，改正后确认：", "",
             cn.elements_digest(elements)]
    if state.get("extract_note"):
        lines += ["", "⚠ " + state["extract_note"]]

    fatal = [d for d in (state.get("deadline_preview") or [])
             if d.get("fatal") and d.get("status") in ("expired", "urgent", "unknown")]
    if fatal:
        lines += ["", "按目前抽到的日期初判："]
        lines += ["  ⚠ {0}：{1}".format(d["name"], d["message"]) for d in fatal]
    lines += ["", "★「应付款日期」务必填准 —— 优先受偿权的六个月从这天起算。"]
    return "\n".join(lines)


def _apply_elements(state: Dict[str, Any], answer: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """律师的修正覆盖模型的抽取。**人说了算，机器不许还嘴。**"""
    merged = dict(state.get("elements") or {})
    merged.update({k: v for k, v in (data or {}).items() if k in
                   {f["key"] for f in cn.ELEMENT_FIELDS}})
    return {"elements": cn.normalize_elements(merged), "elements_note": answer}


# ================================================================ 3 时限体检


def _deadlines(state: Dict[str, Any]) -> Dict[str, Any]:
    """算时限，并**落到案件上**。

    ★ 落库这一步是 Step 15 补的缺口：以前要素只活在这条流程的 checkpoint 里，
      跑第二条流程要重抽一遍，案件页上也看不见。现在律师确认过的日期
      交给 task 模块，长期挂在案件上做提醒。

    ★ 走可选依赖：没装 task 模块，流程照跑，只是不落库。
    """
    out = {"deadlines": cn.compute_deadlines(
        state.get("elements") or {}, state.get("role") or cn.CONTRACTOR)}

    task = registry.optional_api("task")
    if task is None:
        return out
    principal = _principal_from(state.get("actor_id"))
    case_id = int(state.get("case_id") or 0)
    if principal is None or not case_id:
        return out
    try:
        with session_scope() as session:
            rows = task.generate(session, principal, case_id,
                                 anchors=state.get("elements") or {},
                                 role=state.get("role") or cn.CONTRACTOR)
            out["deadline_saved"] = len(rows)
    except Exception as exc:                       # noqa: BLE001
        logger.exception("时限落库失败")
        out["deadline_save_note"] = "时限没能存到案件上（{0}）".format(exc)[:180]
    return out


# ================================================================ 4 定争议焦点


def _issues(state: Dict[str, Any]) -> Dict[str, Any]:
    return {"issues": cn.pick_issues(
        state.get("elements") or {}, state.get("role") or cn.CONTRACTOR)}


# ================================================================ 5 逐焦点检索


def _citation_dict(c: Any) -> Dict[str, Any]:
    return {
        "source_type": getattr(c, "source_type", ""),
        "source_id": getattr(c, "source_id", ""),
        "title": getattr(c, "title", ""),
        "snippet": getattr(c, "snippet", "")[:300],
        "locator": getattr(c, "locator", ""),
        "effective_status": getattr(c, "effective_status", ""),
    }


def _research(state: Dict[str, Any]) -> Dict[str, Any]:
    """按焦点检索。★ 这一步就是"流程驱动检索"。

    律师不用想「我该搜什么」 —— 搜什么由要素决定的焦点决定，
    检索式写在 construction.ISSUE_RULES 里，是**领域知识**不是临场发挥。
    走纯检索（0 成本、0.1 秒一条），因为检索式本身已经足够精确，
    不需要再花钱让模型改写一遍。
    """
    from kernel.ports.retrieval import RetrievalRequest

    retrieval = container.get("retrieval")
    out: List[Dict[str, Any]] = []
    failures: List[str] = []

    for issue in state.get("issues") or []:
        seen, cites = set(), []
        for q in issue.get("queries") or []:
            try:
                res = retrieval.search(RetrievalRequest(query=q, top_k=MAX_PER_QUERY))
            except Exception as exc:
                failures.append("{0}／{1}：{2}".format(issue["title"], q, exc))
                continue
            for c in res.citations:
                sid = getattr(c, "source_id", "")
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                d = _citation_dict(c)
                d["query"] = q
                cites.append(d)
        out.append({
            "key": issue["key"], "title": issue["title"], "why": issue.get("why", ""),
            "queries": issue.get("queries") or [],
            "citations": cites[:MAX_PER_ISSUE],
            "dropped": len(issue.get("queries") or []) and not cites,
        })

    return {"research": out, "research_failures": failures}


# ================================================================ 6 确认焦点


def _ask_issues(state: Dict[str, Any]) -> str:
    lines = ["本案自动定出以下争议焦点，并已就每个焦点检索了依据。", ""]
    for i, r in enumerate(state.get("research") or [], 1):
        lines.append("{0}. {1}（{2} 条依据）".format(i, r["title"], len(r["citations"])))
        lines.append("   为什么查：{0}".format(r.get("why", "")))
        for c in r["citations"][:3]:
            lines.append("   · {0}{1}".format(
                c["source_id"], "（{0}）".format(c["effective_status"])
                if c["effective_status"] else ""))
        lines.append("")

    dl = [d for d in (state.get("deadlines") or [])
          if d.get("status") in ("expired", "urgent", "unknown")]
    if dl:
        lines.append("时限提醒：")
        lines += ["  {0} {1}：{2}".format(
            "⛔" if d["status"] == "expired" else "⚠", d["name"], d["message"]) for d in dl]
        lines.append("")

    if state.get("research_failures"):
        lines.append("⚠ 有 {0} 条检索没跑成功，依据可能不全。".format(
            len(state["research_failures"])))
        lines.append("")

    lines.append("确认后将据此起草法律意见书。要去掉某个焦点，"
                 "在结构化数据里传 drop:[焦点key]。")
    return "\n".join(lines)


def _apply_issues(state: Dict[str, Any], answer: str, data: Dict[str, Any]) -> Dict[str, Any]:
    drop = set(data.get("drop") or [])
    kept = [r for r in (state.get("research") or []) if r["key"] not in drop]
    return {"research": kept, "issues_note": answer}


# ================================================================ 7 起草


def _draft(state: Dict[str, Any]) -> Dict[str, Any]:
    """起草法律意见书。**要判断，所以开思考。**"""
    from kernel.ports.model import ChatMessage

    role = state.get("role") or cn.CONTRACTOR
    elements = state.get("elements") or {}

    # 依据编号 S1、S2… 让模型只能引已有的，引不到就没法编
    refs, n = [], 0
    for r in state.get("research") or []:
        for c in r["citations"]:
            n += 1
            c["ordinal"] = n
            refs.append("[S{0}] {1}{2}\n{3}".format(
                n, c["source_id"],
                "（{0}）".format(c["effective_status"]) if c["effective_status"] else "",
                c["snippet"][:220]))
    outline = "\n".join("{0}. {1}".format(i, r["title"])
                        for i, r in enumerate(state.get("research") or [], 1))
    warn = "\n".join("· {0}：{1}（{2}）".format(d["name"], d["message"], d["basis"])
                     for d in (state.get("deadlines") or [])
                     if d.get("status") in ("expired", "urgent", "unknown"))

    prompt = (
        "为下面这起建设工程施工合同纠纷起草一份法律意见书。我方是{role}。\n\n"
        "【已确认要素】\n{elements}\n\n"
        "【争议焦点】\n{outline}\n\n"
        "【时限】\n{warn}\n\n"
        "【可引用的依据】（**只能引这些**，用【S数字】标注）\n{refs}\n\n"
        "要求：\n"
        "1. 按争议焦点分节，每节先给结论再给理由；\n"
        "2. 每个结论后面必须标注依据编号，**不得引用未列出的法条或案例**；\n"
        "3. 时限有问题的，放在最前面单列一节提示；\n"
        "4. 最后给「下一步建议」，写具体动作，不要写空话。"
    ).format(role=cn.ROLE_LABELS.get(role, role),
             elements=cn.elements_digest(elements) or "（无）",
             outline=outline or "（无）", warn=warn or "（无异常）",
             refs="\n\n".join(refs) or "（无 —— 请在意见书中说明依据不足）")

    system = ChatMessage(
        role="system",
        content="你是资深建设工程律师。只用给定依据，宁可说「依据不足」也不编条号。")
    user = ChatMessage(role="user", content=prompt)
    model = container.get("model")
    spent = float(state.get("cost_cny") or 0)

    try:
        r = model.chat([system, user], thinking=True, effort="high",
                       temperature=0.3, max_tokens=DRAFT_MAX_TOKENS)
        spent += _cost_cny(r)

        if not (r.text or "").strip():
            # ★ 开着思考时，**推理 token 也吃输出配额**，而它吃多少是不可预测的：
            #   同样的提示词，这次 8000 够、下次就不够。靠调大配额只是把墙往后挪。
            #
            #   所以退一步：**关掉思考重试一次**。起草是"把给定材料组织成文"，
            #   资料都已经在提示词里了，不开思考照样写得出来 —— 质量略降，
            #   但比交一份空白强得多。
            logger.warning("起草正文为空，关闭思考重试一次")
            r = model.chat([system, user], thinking=False,
                           temperature=0.3, max_tokens=DRAFT_MAX_TOKENS)
            spent += _cost_cny(r)
            if (r.text or "").strip():
                return {"draft": r.text, "cost_cny": spent,
                        "draft_note": "首次起草因推理占满配额返回空白，"
                                      "已关闭思考模式重新生成。"}

        if not (r.text or "").strip():
            return {"draft": "", "cost_cny": spent,
                    "draft_error": "模型两次都返回空正文（推理可能仍占满输出配额，"
                                   "max_tokens={0}）。请缩减争议焦点后重试，"
                                   "或手工起草。".format(DRAFT_MAX_TOKENS)}
        return {"draft": r.text, "cost_cny": spent}
    except Exception as exc:
        logger.exception("起草失败")
        return {"draft": "", "draft_error": str(exc)}


# ================================================================ 8 终审


def _ask_signoff(state: Dict[str, Any]) -> str:
    if state.get("draft_error"):
        return "起草失败：{0}\n请确认后关闭流程，或修正后重跑。".format(state["draft_error"])
    tail = ("★ 确认即表示你已核对全部引用 —— 这句确认会连同你的姓名一起留痕。")
    if state.get("draft_note"):
        # 降级生成的稿子要标出来 —— 律师有权知道这一版是怎么来的
        return ("意见书草稿已生成（{0} 字）。\n⚠ {1}\n\n请通读后确认。\n\n{2}"
                ).format(len(state.get("draft") or ""), state["draft_note"], tail)
    return ("意见书草稿已生成（{0} 字）。请通读后确认。\n\n{1}"
            ).format(len(state.get("draft") or ""), tail)


def _apply_signoff(state: Dict[str, Any], answer: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """律师终审通过 —— **这时候才归档**。

    ★ 归档发生在终审之后而不是起草之后：案卷里躺的应该是律师认过的东西，
      不是模型刚吐出来的草稿。中间那些版本留在 checkpoint 里就够了。

    ★ 走**可选依赖**：没装 doc 模块，流程照样跑完，只是不归档。
      业务不该因为一个可选模块缺席就走不下去。
    """
    final = data.get("draft") or state.get("draft") or ""
    out: Dict[str, Any] = {"final": final, "signoff_note": answer}
    if not final.strip():
        # ★ 没归档必须说明白。静默返回等于让律师以为「存好了」——
        #   这正是本项目一再吃亏的那种错：空结果不会引起怀疑。
        out["archive_note"] = "草稿为空，没有可归档的内容（{0}）".format(
            state.get("draft_error") or "原因不明")
        return out

    doc = registry.optional_api("doc")
    if doc is None:
        out["archive_note"] = "未装 doc 模块，意见书没有归档"
        return out

    # 终审这一刻是谁在签字，以服务端注入的为准
    principal = _principal_from(data.get("__actor_id__") or state.get("actor_id"))
    if principal is None:
        out["archive_note"] = "缺少归档所需的身份信息，未归档"
        return out

    try:
        with session_scope() as session:
            row, ver = doc.save_text(
                session, principal, int(state.get("case_id") or 0),
                title="法律意见书（{0}）".format(
                    (state.get("elements") or {}).get("project") or "本案"),
                text=final,
                folder="法律意见",
                note="流程 {0} 终审通过后自动归档；律师确认：{1}".format(
                    (state.get("run_id") or "")[:8], answer[:120]),
            )
            out["archived_document_id"] = row.id
            out["archived_version"] = ver.version
            out["archive_note"] = "已归档进案卷「法律意见」"
    except Exception as exc:                      # noqa: BLE001
        # ★ 归档失败不能把终审判失败 —— 律师已经签过字了，
        #   那个事实比归档重要。失败要说出来，让人手工存一份。
        logger.exception("意见书归档失败")
        out["archive_note"] = "归档失败（{0}），请手工保存".format(exc)[:200]
    return out


def _principal_from(uid):
    """还原办案人身份，用来过 doc 的权限。

    流程跑在后台线程里，没有请求上下文，所以身份必须**随 state 走**。
    """
    if not uid:
        return None
    try:
        with session_scope() as session:
            # ★ 跨模块只能走 api.py —— 边界检查会拦 modules.identity.access
            return registry.api("identity").build_principal(session, int(uid))
    except Exception:                             # noqa: BLE001
        logger.exception("还原身份失败")
        return None


# ================================================================ 模板


CONSTRUCTION = Template(
    key="construction.dispute",
    label="建设工程施工合同纠纷（承包人／发包人）",
    requires=("facts",),
    steps=[
        Step(key="extract", title="抽取案情要素", run=_extract),
        Step(key="confirm_elements", title="律师确认要素",
             ask=_ask_elements, apply=_apply_elements),
        Step(key="deadlines", title="时限体检", run=_deadlines),
        Step(key="issues", title="确定争议焦点", run=_issues),
        Step(key="research", title="逐焦点检索依据", run=_research),
        Step(key="confirm_issues", title="律师确认焦点与依据",
             ask=_ask_issues, apply=_apply_issues),
        Step(key="draft", title="起草法律意见书", run=_draft),
        Step(key="signoff", title="律师终审", ask=_ask_signoff, apply=_apply_signoff),
    ],
)

TEMPLATES = [CONSTRUCTION]

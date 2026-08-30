# -*- coding: utf-8 -*-
"""文书生成的业务层。

**防幻觉靠的是校验，不是祈祷。**

提示词里写「只能引用给定依据」是必要的，但那只是请求。真正管用的是
生成之后**逐个核对**：文里出现的每一个【S数字】，是不是真的在给它的
依据清单里？不在就是编的，当场标出来。

律师敢在 AI 起草的文书上签字的前提，是他能验；能验的前提，是我们先验过一遍。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel.config import settings
from kernel.container import container
from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import Principal
from modules.draft import templates as tpl
from modules.draft.models import Draft, DraftSection

logger = logging.getLogger(__name__)

#: 生成一节给多少输出配额。★ 开思考时推理也吃配额（Step 10 踩过两次），
#: 所以起草一律**不开思考** —— 材料都在提示词里，这是组织语言不是推理。
SECTION_MAX_TOKENS = 3000

#: 文里的引用编号长这样：【S3】
CITE_RE = re.compile(r"【S(\d+)】")

#: 当事人角色的中文写法。★ 文书里印 "plaintiff" 是递不出去的 ——
#: 库里存英文枚举是对的（稳定、好比对），但落到纸上必须是中文。
ROLE_CN = {
    "plaintiff": "原告", "defendant": "被告", "third_party": "第三人",
    "appellant": "上诉人", "appellee": "被上诉人", "other": "",
}


def _model():
    return container.get("model")


def _case_api():
    return registry.api("case")


def _cost_cny(result) -> float:
    usd = float((getattr(result, "usage", None) or {}).get("cost_usd", 0) or 0)
    return usd * settings.usd_cny_rate


# ================================================================ 引用校验


def _case_writable(session, principal, case_id) -> None:
    """写操作走这句：看得见 **且** 写得动。归档/终止的案子只读。"""
    if case_id:
        _case_api().assert_writable(session, principal, case_id)


def verify_citations(
    text: str,
    provided: List[Dict[str, Any]],
    needs_citation: bool = True,
) -> Tuple[List[int], List[str]]:
    """核对文里的引用编号。返回 (实际用到的编号, 告警)。

    `needs_citation=False` 用于**本来就不需要引法条**的段落，比如
    「诉讼请求」（那是请求清单）、「事项与范围」（那是说明）。
    对这些段落报「没有引用依据」是噪音 —— 而噪音会让人开始忽略告警，
    等真的编造出现时也一起忽略掉。

    ★ **这是整个模块的立身之本。**

    三种要报的情况：

      1. 引了不存在的编号 —— 只给了 S1-S5 却写了【S7】。**这就是编的**
      2. 一条依据都没引 —— 论述型段落不给依据，等于凭空说话
      3. 引到了已废止/失效的条文 —— 内容对得上，但不能用了
    """
    warns: List[str] = []
    used = sorted({int(m) for m in CITE_RE.findall(text or "")})
    n = len(provided)

    bogus = [u for u in used if u < 1 or u > n]
    if bogus:
        warns.append(
            "引用了不存在的依据编号 {0} —— 手里只有 S1–S{1}，**这些是编的**".format(
                "、".join("S{0}".format(b) for b in bogus), n or 0))

    real = [u for u in used if 1 <= u <= n]
    if needs_citation and not real and (text or "").strip():
        warns.append("整段没有引用任何依据 —— 法律论断需要出处")

    for u in real:
        c = provided[u - 1]
        status = str(c.get("effective_status") or "")
        if "失效" in status or "废止" in status:
            warns.append("S{0}（{1}）已{2}，不能再作为依据".format(
                u, c.get("source_id", ""), status))
    return real, warns


# ================================================================ 依据


def _citations_from_run(session: Session, principal: Principal,
                        run_id: str) -> List[Dict[str, Any]]:
    """从流程跑出来的检索结果里取依据。"""
    wf = registry.optional_api("workflow")
    if wf is None:
        return []
    state = wf.live_state(session, principal, run_id)
    out: List[Dict[str, Any]] = []
    seen = set()
    for issue in state.get("research") or []:
        for c in issue.get("citations") or []:
            sid = c.get("source_id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            out.append({
                "source_type": c.get("source_type", ""),
                "source_id": sid,
                "title": c.get("title", ""),
                "snippet": (c.get("snippet") or "")[:400],
                "locator": c.get("locator", ""),
                "effective_status": c.get("effective_status", ""),
                "corpus_version": state.get("corpus_version", ""),
                "trace_id": state.get("trace_id", ""),
                "issue": issue.get("title", ""),
            })
    return out


def _citations_from_search(queries: List[str], top_k: int = 4) -> List[Dict[str, Any]]:
    """自己检索一轮拿依据。"""
    from kernel.ports.retrieval import RetrievalRequest

    retrieval = container.get("retrieval")
    out: List[Dict[str, Any]] = []
    seen = set()
    for q in queries:
        try:
            res = retrieval.search(RetrievalRequest(query=q, top_k=top_k))
        except Exception:                          # noqa: BLE001
            logger.exception("起草取依据失败：%s", q)
            continue
        for c in res.citations:
            sid = getattr(c, "source_id", "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            out.append({
                "source_type": getattr(c, "source_type", ""),
                "source_id": sid,
                "title": getattr(c, "title", ""),
                "snippet": (getattr(c, "snippet", "") or "")[:400],
                "locator": getattr(c, "locator", ""),
                "effective_status": getattr(c, "effective_status", ""),
                # ★ 溯源要靠这两个：半年后库重建了，还得说清
                #   「当时是哪一版语料、哪一次检索」
                "corpus_version": getattr(res, "corpus_version", ""),
                "trace_id": getattr(res, "trace_id", ""),
                "issue": q,
            })
    return out


# ================================================================ 填充


def _party_lines(session: Session, principal: Principal, case_id: int,
                 ours: bool) -> Tuple[str, str]:
    """列当事人。**直接取案件数据，不过模型。**

    让模型"生成"当事人名称，等于让它照上下文猜一个名字。
    猜错一个字，立案窗口就退回。
    """
    crm = registry.optional_api("crm")
    rows = _case_api().parties_of(session, case_id)
    picked = [p for p in rows if bool(p.is_ours) == ours]
    if not picked:
        return "", "案件里没有登记{0}当事人".format("我方" if ours else "对方")

    lines = []
    for p in picked:
        name = "主体{0}".format(p.party_id)
        if crm is not None:
            try:
                name = crm.get_party(session, p.party_id).name
            except Exception:                      # noqa: BLE001
                pass
        role = ROLE_CN.get(getattr(p, "role", ""), "")
        lines.append("{0}{1}".format(name, "（{0}）".format(role) if role else ""))
    return "\n".join(lines), ""


def _fill_value(session: Session, principal: Principal, case, source: str) -> Tuple[str, str]:
    """按 source 从案件/案卷里取值。返回 (内容, 缺什么)。"""
    if source == "our_parties":
        return _party_lines(session, principal, case.id, True)
    if source == "their_parties":
        return _party_lines(session, principal, case.id, False)
    if source == "court":
        return (case.court or ""), ("" if case.court else "案件没有填法院")
    if source == "client_name":
        text, miss = _party_lines(session, principal, case.id, True)
        return text, miss
    if source == "evidence_list":
        doc = registry.optional_api("doc")
        if doc is None:
            return "", "未装 doc 模块，取不到证据清单"
        rows = doc.list_documents(session, principal, case.id, folder="证据材料")
        if not rows:
            return "", "案卷「证据材料」里还没有材料"
        return "\n".join("{0}. {1}".format(i, d.title)
                         for i, d in enumerate(rows, 1)), ""
    return "", "不认识的取值来源：{0}".format(source)


def _case_brief(session: Session, principal: Principal, case) -> str:
    """喂给模型的案件背景。只放事实，不放判断。"""
    ours, _ = _party_lines(session, principal, case.id, True)
    theirs, _ = _party_lines(session, principal, case.id, False)
    bits = [
        "案件：{0}".format(case.title),
        "我方：{0}".format(ours.replace("\n", "、") or "未登记"),
        "对方：{0}".format(theirs.replace("\n", "、") or "未登记"),
    ]
    if case.court:
        bits.append("法院：{0}".format(case.court))
    if case.amount is not None:
        bits.append("标的额：{0}".format(case.amount))
    if case.opened_on:
        bits.append("立案日：{0}".format(case.opened_on))
    if case.summary:
        bits.append("案情：{0}".format(case.summary))
    return "\n".join(bits)


# ================================================================ 创建


def create(
    session: Session,
    principal: Principal,
    case_id: int,
    template_key: str,
    title: str = "",
    run_id: str = "",
    queries: Optional[List[str]] = None,
) -> Draft:
    """建一份文书草稿，并把可引用的依据固定下来。"""
    _case_api().assert_writable(session, principal, case_id)
    t = tpl.get(template_key)
    if t is None:
        raise NotFound("没有这个文书模板：{0}".format(template_key),
                       code="draft.no_template")

    cites: List[Dict[str, Any]] = []
    if run_id:
        cites = _citations_from_run(session, principal, run_id)
    if not cites and queries:
        cites = _citations_from_search(queries)

    row = Draft(
        tenant_id=principal.tenant_id,
        case_id=case_id,
        template_key=template_key,
        title=(title or t.label).strip()[:255],
        source_run_id=run_id,
        citations=json.dumps(cites, ensure_ascii=False),
        created_by=principal.user_id,
    )
    session.add(row)
    session.flush()

    # ★ 依据快照交给 citation 模块保管 —— 它负责「半年后还原得回来」
    registry.api("citation").attach(session, principal, "draft", row.id, cites)

    case = _case_api().get_case(session, case_id)
    for i, s in enumerate(t.sections, 1):
        content, note, source = "", "", "empty"
        if s.kind == tpl.FILL:
            # ★ 建的时候就把能填的填掉 —— 这些不需要等模型
            content, note = _fill_value(session, principal, case, s.source)
            source = "fill" if content else "empty"
        session.add(DraftSection(
            draft_id=row.id, ordinal=i, key=s.key, title=s.title, kind=s.kind,
            content=content, source=source, note=note[:255],
        ))
    session.flush()
    return row


# ================================================================ 生成


def generate_section(
    session: Session,
    principal: Principal,
    draft_id: int,
    section_key: str,
    instruction: str = "",
    force: bool = False,
) -> DraftSection:
    """生成（或重新生成）一节。

    ★ 律师亲手改过的段落，默认**不覆盖** —— 要覆盖得显式 force。
      辛苦改完的东西被一键重生成冲掉，是最让人火大的那种 bug。
    """
    row = get_draft(session, principal, draft_id)
    sec = _section(session, draft_id, section_key)
    t = tpl.get(row.template_key)
    spec = next((s for s in (t.sections if t else []) if s.key == section_key), None)
    if spec is None:
        raise NotFound("这份文书里没有这一节", code="draft.no_section")

    if spec.kind == tpl.MANUAL:
        raise DomainError("这一节由律师手写，平台不代写", code="draft.manual_only")

    case = _case_api().get_case(session, row.case_id)

    if spec.kind == tpl.FILL:
        content, note = _fill_value(session, principal, case, spec.source)
        sec.content = content
        sec.note = note[:255]
        sec.source = "fill" if content else "empty"
        sec.edited = False
        sec.updated_by = principal.user_id
        session.flush()
        return sec

    if sec.edited and not force:
        raise DomainError(
            "这一节你已经手工改过了。重新生成会覆盖掉你的修改 —— "
            "确认要覆盖请再点一次。",
            code="draft.would_overwrite",
        )

    cites: List[Dict[str, Any]] = json.loads(row.citations or "[]")
    refs = "\n\n".join(
        "[S{0}] {1}{2}\n{3}".format(
            i, c.get("source_id", ""),
            "（{0}）".format(c["effective_status"]) if c.get("effective_status") else "",
            (c.get("snippet") or "")[:260])
        for i, c in enumerate(cites, 1))

    written = [
        "【{0}】\n{1}".format(s.title, s.content)
        for s in sections_of(session, draft_id)
        if s.key != section_key and s.content.strip()
    ]

    prompt = (
        "你在起草一份《{doc}》中的「{title}」一节。\n\n"
        "【本节要求】\n{brief}\n{extra}\n\n"
        "【案件事实】\n{brief_case}\n\n"
        "【文书其余部分（供衔接，不要重复）】\n{written}\n\n"
        "【可引用的依据】（**只能引这些**，用【S数字】标注）\n{refs}\n\n"
        "只输出本节正文，不要写小标题，不要解释你在做什么。"
    ).format(
        doc=t.label, title=spec.title, brief=spec.brief,
        extra=("\n补充要求：" + instruction) if instruction.strip() else "",
        brief_case=_case_brief(session, principal, case),
        written="\n\n".join(written) or "（暂无）",
        refs=refs or "（没有依据 —— 请在正文中写明「依据不足」，不要自行引用法条）",
    )

    from kernel.ports.model import ChatMessage

    try:
        r = _model().chat(
            [ChatMessage(role="system",
                         content="你是资深中国律师，起草诉讼文书。"
                                 "只使用给定的依据，宁可写「依据不足」也绝不编造法条或案例编号。"
                                 "语言正式、简洁，符合中国法律文书习惯。"),
             ChatMessage(role="user", content=prompt)],
            # ★ 起草不开思考：材料都在提示词里，这是组织语言不是推理。
            #   Step 10 两次栽在「推理吃光输出配额、正文返回空串」上。
            thinking=False, temperature=0.3, max_tokens=SECTION_MAX_TOKENS,
        )
    except Exception as exc:                       # noqa: BLE001
        logger.exception("起草失败")
        raise DomainError("起草失败：{0}".format(exc), code="draft.model_failed") from exc

    text = (r.text or "").strip()
    if not text:
        raise DomainError(
            "模型返回空正文。请缩短本节要求后重试，或手工起草。",
            code="draft.empty_output",
        )

    used, warns = verify_citations(text, cites, needs_citation=spec.needs_citation)
    # ★ 锚点从正文里的【S数字】推出来，正文一变就重算 —— 偏移量永不漂移
    registry.api("citation").reanchor(session, "draft", row.id, section_key, text)
    sec.content = text
    sec.source = "model"
    sec.edited = False
    sec.used = json.dumps(used)
    sec.warnings = json.dumps(warns, ensure_ascii=False)
    sec.note = ""
    sec.updated_by = principal.user_id
    row.cost_cny = str(round(float(row.cost_cny or 0) + _cost_cny(r), 6))
    session.flush()
    return sec


def edit_section(session: Session, principal: Principal, draft_id: int,
                 section_key: str, content: str) -> DraftSection:
    """律师手工改一节。改完重新校验引用 —— 他也可能手滑写错编号。"""
    row = get_draft(session, principal, draft_id)
    sec = _section(session, draft_id, section_key)
    cites = json.loads(row.citations or "[]")
    t = tpl.get(row.template_key)
    spec = next((x for x in (t.sections if t else []) if x.key == section_key), None)
    used, warns = verify_citations(
        content, cites, needs_citation=(spec.needs_citation if spec else True))
    registry.api("citation").reanchor(session, "draft", row.id, section_key, content)
    sec.content = content
    sec.source = "manual"
    sec.edited = True
    sec.used = json.dumps(used)
    sec.warnings = json.dumps(warns, ensure_ascii=False)
    sec.updated_by = principal.user_id
    session.flush()
    return sec


# ================================================================ 组装与定稿


def assemble(session: Session, principal: Principal, draft_id: int) -> str:
    """把各节拼成完整文书。"""
    row = get_draft(session, principal, draft_id)
    parts = ["# {0}".format(row.title), ""]
    for s in sections_of(session, draft_id):
        if not s.content.strip():
            continue
        parts.append("## {0}".format(s.title))
        parts.append(s.content.strip())
        parts.append("")
    cites = json.loads(row.citations or "[]")
    if cites:
        parts.append("## 依据")
        for i, c in enumerate(cites, 1):
            parts.append("[S{0}] {1}{2}".format(
                i, c.get("source_id", ""),
                "（{0}）".format(c["effective_status"]) if c.get("effective_status") else ""))
    return "\n".join(parts).strip()


def readiness(session: Session, principal: Principal, draft_id: int) -> Dict[str, Any]:
    """能不能定稿。**把拦路的东西一次说全**，别让人点一次报一个。"""
    row = get_draft(session, principal, draft_id)
    t = tpl.get(row.template_key)
    blocking: List[str] = []
    warns: List[str] = []

    for s in sections_of(session, draft_id):
        spec = next((x for x in (t.sections if t else []) if x.key == s.key), None)
        if spec is not None and spec.required and not s.content.strip():
            blocking.append("「{0}」还是空的{1}".format(
                s.title, "（{0}）".format(s.note) if s.note else ""))
        for w in json.loads(s.warnings or "[]"):
            warns.append("「{0}」：{1}".format(s.title, w))

    return {"ready": not blocking, "blocking": blocking, "warnings": warns}


def finalize(session: Session, principal: Principal, draft_id: int,
             note: str = "") -> Draft:
    """定稿并归档进案卷。

    ★ 定稿前**必须**过一遍引用校验的硬拦截：引了不存在的编号绝不放行。
      这是「敢签字」的底线 —— 一份带编造条号的文书递出去，是执业事故。
    """
    row = get_draft(session, principal, draft_id)
    if row.status == "final":
        raise DomainError("这份文书已经定稿了", code="draft.already_final")

    state = readiness(session, principal, draft_id)
    if state["blocking"]:
        raise DomainError(
            "还不能定稿：{0}".format("；".join(state["blocking"])),
            code="draft.not_ready",
        )
    fake = [w for w in state["warnings"] if "这些是编的" in w]
    if fake:
        raise DomainError(
            "文书里有编造的依据编号，必须先改掉：{0}".format("；".join(fake)),
            code="draft.fabricated_citation",
        )

    # ★ 定稿前跑一次引用体检 —— 定稿是签字点，是最后一次能发现
    #   「你引的这条已经被修订/废止了」的机会。
    #
    #   ★ **只警告不拦截**，和上面那两条硬拦不同：
    #     编造条号是律师自己的错，必须拦；法条被修订是外面的世界变了，
    #     律师完全可能明知道而仍要引旧版（比如按行为时法）。
    #     替他决定，就是替他扛责任。
    #
    #   查不动（检索服务挂了）也不拦 —— 一份文书不该因为检索服务
    #   没起来就定不了稿。
    citation = registry.optional_api("citation")
    if citation is not None:
        try:
            chk = citation.recheck(session, principal, "draft", draft_id)
            bad = chk["changed"] + chk["expired"] + chk["gone"]
            if bad:
                logger.warning(
                    "文书 %s 定稿时有 %s 条引用已变化（改动 %s / 失效 %s / 找不到 %s）",
                    draft_id, bad, chk["changed"], chk["expired"], chk["gone"])
                row.recheck_note = (
                    "定稿时体检：{0} 条引用已变化"
                    "（原文改动 {1} · 已失效 {2} · 库里找不到 {3}）".format(
                        bad, chk["changed"], chk["expired"], chk["gone"]))[:255]
            else:
                row.recheck_note = ""
        except Exception as exc:                         # noqa: BLE001
            logger.warning("定稿时引用体检失败：%s", exc)

    text = assemble(session, principal, draft_id)
    doc = registry.optional_api("doc")
    if doc is not None:
        try:
            folder = "法律意见" if row.template_key == "legal_opinion" else "诉讼文书"
            d, _ = doc.save_text(
                session, principal, row.case_id, row.title, text,
                folder=folder,
                note="文书定稿归档；{0}".format(note or "")[:255],
            )
            row.document_id = d.id
        except Exception as exc:                   # noqa: BLE001
            logger.exception("文书归档失败")
            raise DomainError(
                "定稿失败：归档进案卷时出错（{0}）".format(exc),
                code="draft.archive_failed",
            ) from exc

    row.status = "final"
    row.finalized_by = principal.user_id
    row.finalized_at = datetime.now(timezone.utc)
    session.flush()
    return row


# ================================================================ 读


def get_draft(session: Session, principal: Principal, draft_id: int) -> Draft:
    row = session.scalar(
        select(Draft).where(Draft.id == draft_id,
                            Draft.tenant_id == principal.tenant_id)
    )
    if row is None:
        raise NotFound("文书不存在", code="draft.not_found")
    _case_api().assert_visible(session, principal, row.case_id)
    return row


def parts_of(session: Session, draft_id: int) -> List[Tuple[str, str, str]]:
    """给 citation 模块用：[(节 key, 节标题, 正文)]。"""
    return [(s.key, s.title, s.content) for s in sections_of(session, draft_id)]


def sections_of(session: Session, draft_id: int) -> List[DraftSection]:
    return list(session.scalars(
        select(DraftSection)
        .where(DraftSection.draft_id == draft_id)
        .order_by(DraftSection.ordinal)
    ).all())


def _section(session: Session, draft_id: int, key: str) -> DraftSection:
    sec = session.scalar(
        select(DraftSection).where(DraftSection.draft_id == draft_id,
                                   DraftSection.key == key)
    )
    if sec is None:
        raise NotFound("这份文书里没有这一节：{0}".format(key), code="draft.no_section")
    return sec


def drafts_of_case(session: Session, principal: Principal, case_id: int) -> List[Draft]:
    _case_api().assert_visible(session, principal, case_id)
    return list(session.scalars(
        select(Draft)
        .where(Draft.tenant_id == principal.tenant_id, Draft.case_id == case_id)
        .order_by(Draft.id.desc())
    ).all())


def citations_of(session: Session, principal: Principal, draft_id: int) -> List[Dict[str, Any]]:
    return json.loads(get_draft(session, principal, draft_id).citations or "[]")


def void(session: Session, principal: Principal, draft_id: int) -> Draft:
    row = get_draft(session, principal, draft_id)
    if row.status == "final":
        raise DomainError("定稿的文书不能作废 —— 它已经归档了", code="draft.is_final")
    row.status = "void"
    session.flush()
    return row


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的文书。**只列定稿的** —— 草稿不进卷。"""
    _case_api().assert_visible(session, principal, case_id)
    rows = drafts_of_case(session, principal, case_id)
    finals = [d for d in rows if d.status == "final"]
    out = []
    if finals:
        out.append({"name": "定稿文书", "count": len(finals), "note": ""})
    return out


def archive_blockers(session, principal, case_id):
    """★ 有草稿没定稿 —— 归档后就再也定不了了。"""
    _case_api().assert_visible(session, principal, case_id)
    drafts = [d for d in drafts_of_case(session, principal, case_id)
              if d.status not in ("final", "void")]
    if drafts:
        return [{"what": "还有 {0} 份文书没定稿".format(len(drafts)),
                 "how": "定稿或作废 —— 归档后就改不了也定不了了"}]
    return []

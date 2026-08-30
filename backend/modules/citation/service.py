# -*- coding: utf-8 -*-
"""引用留痕的业务层。

验收标准是一句话：**点文书任一句，看到出处、原文片段、时效状态。**

要做到这个，得回答三个问题：

  1. 这句话有没有依据？          → 句子级的锚点
  2. 这条依据当时长什么样？      → 快照（原文片段 + 时效 + 语料版本）
  3. 它是怎么被找出来的？        → trace_id 关联回那次检索

第 3 条最容易被省掉，但它才是「可核查」的关键 ——
律师问「你这条哪来的」，答案得是「2026-08-26 那次检索，语料版本 xxx，
第几条命中」，而不是「模型说的」。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.security import Principal
from modules.citation.models import Anchor, Citation

logger = logging.getLogger(__name__)

#: 文里的引用编号
CITE_RE = re.compile(r"【S(\d+)】")

#: 断句。中文按句号/问号/感叹号/分号/换行断，**保留标点位置**以便算偏移
SENT_END = "。！？；!?;\n"

#: 像是法律论断的词。★ 这是**启发式提示**，不是判定 ——
#: 命中只说明「这句话看着像在下法律结论，但没标依据，建议核对」，
#: 不代表它一定错。把它说成判定会让律师失去信任，比不提示更糟。
ASSERTION_HINTS = (
    "应当", "不予支持", "应予支持", "依法", "有权", "无效", "承担责任",
    "承担连带", "视为", "不得", "构成", "违反", "予以驳回", "享有",
)


# ================================================================ 断句


def split_sentences(text: str) -> List[Tuple[int, int, str]]:
    """把一段正文断成句子，返回 [(起, 止, 原文)]。

    ★ 必须带偏移量。只返回句子文本的话，就没法把「第几个字符」
      映射回「第几句」，也就做不到点句子看出处。
    """
    out: List[Tuple[int, int, str]] = []
    start = 0
    for i, ch in enumerate(text or ""):
        if ch in SENT_END:
            seg = text[start:i + 1]
            if seg.strip():
                out.append((start, i + 1, seg))
            start = i + 1
    tail = (text or "")[start:]
    if tail.strip():
        out.append((start, len(text or ""), tail))
    return out


# ================================================================ 快照依据


def attach(
    session: Session,
    principal: Principal,
    doc_kind: str,
    doc_id: int,
    sources: Iterable[Dict[str, Any]],
) -> List[Citation]:
    """把可引用的依据快照进来，按顺序编号（1 就是【S1】）。

    重复调用会**整体替换** —— 依据清单变了，之前的锚点也就无效了，
    一起清掉重来比留着半旧半新安全。
    """
    if doc_kind not in ("draft", "workflow"):
        raise DomainError("不认识的文书类型：{0}".format(doc_kind),
                          code="citation.bad_doc_kind")

    session.execute(delete(Anchor).where(Anchor.doc_kind == doc_kind,
                                         Anchor.doc_id == doc_id))
    session.execute(delete(Citation).where(Citation.doc_kind == doc_kind,
                                           Citation.doc_id == doc_id,
                                           Citation.tenant_id == principal.tenant_id))
    session.flush()

    rows: List[Citation] = []
    for i, s in enumerate(sources, 1):
        row = Citation(
            tenant_id=principal.tenant_id,
            doc_kind=doc_kind, doc_id=doc_id, ordinal=i,
            source_type=str(s.get("source_type") or "")[:16],
            source_id=str(s.get("source_id") or "")[:255],
            title=str(s.get("title") or "")[:255],
            snippet=str(s.get("snippet") or ""),
            locator=str(s.get("locator") or "")[:255],
            effective_status=str(s.get("effective_status") or "")[:64],
            corpus_version=str(s.get("corpus_version") or "")[:64],
            trace_id=str(s.get("trace_id") or "")[:64],
            issue=str(s.get("issue") or "")[:255],
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def sources_of(session: Session, doc_kind: str, doc_id: int) -> List[Citation]:
    return list(session.scalars(
        select(Citation)
        .where(Citation.doc_kind == doc_kind, Citation.doc_id == doc_id)
        .order_by(Citation.ordinal)
    ).all())


# ================================================================ 定位


def reanchor(session: Session, doc_kind: str, doc_id: int,
             part_key: str, text: str) -> List[Anchor]:
    """重算某一节的锚点。**正文一变就要调一次。**

    ★ 锚点是从正文里的【S数字】推出来的，不是手工维护的偏移量。
      手工维护的偏移量在第一次编辑之后就全错了；推导的永远是对的。
    """
    cites = {c.ordinal: c for c in sources_of(session, doc_kind, doc_id)}
    session.execute(delete(Anchor).where(
        Anchor.doc_kind == doc_kind, Anchor.doc_id == doc_id,
        Anchor.part_key == part_key))
    session.flush()

    sentences = split_sentences(text or "")
    out: List[Anchor] = []
    for m in CITE_RE.finditer(text or ""):
        ordinal = int(m.group(1))
        cite = cites.get(ordinal)
        if cite is None:
            # 引了不存在的编号 —— draft 那边已经报过「这些是编的」，
            # 这里不重复报警，只是不给它落锚点（它本来就没有来源）
            continue
        si, ss, se, stext = 0, m.start(), m.end(), ""
        for idx, (a, b, seg) in enumerate(sentences):
            if a <= m.start() < b:
                si, ss, se, stext = idx, a, b, seg
                break
        row = Anchor(
            citation_id=cite.id, doc_kind=doc_kind, doc_id=doc_id,
            part_key=part_key, start=m.start(), end=m.end(),
            sentence_index=si, sentence_start=ss, sentence_end=se,
            sentence_text=stext.strip()[:2000],
        )
        session.add(row)
        out.append(row)
    session.flush()
    return out


def clear(session: Session, doc_kind: str, doc_id: int) -> None:
    session.execute(delete(Anchor).where(Anchor.doc_kind == doc_kind,
                                         Anchor.doc_id == doc_id))
    session.execute(delete(Citation).where(Citation.doc_kind == doc_kind,
                                           Citation.doc_id == doc_id))
    session.flush()


def _segments(sentence: str) -> List[Dict[str, Any]]:
    """把一句话切成 [{"t": 文字} | {"cite": 编号}]，供界面原位渲染。"""
    out: List[Dict[str, Any]] = []
    pos = 0
    for m in CITE_RE.finditer(sentence):
        if m.start() > pos:
            out.append({"t": sentence[pos:m.start()]})
        out.append({"cite": int(m.group(1))})
        pos = m.end()
    if pos < len(sentence):
        out.append({"t": sentence[pos:]})
    return out


# ================================================================ 句子地图


def sentence_map(
    session: Session,
    doc_kind: str,
    doc_id: int,
    parts: List[Tuple[str, str, str]],
) -> List[Dict[str, Any]]:
    """给界面用：每一节的每一句，带上它引了哪些依据。

    `parts` 是 [(节 key, 节标题, 正文)]。

    ★ **没有依据的句子也要返回**，并标出来。
      「哪句话没有出处」比「哪句话有出处」更值得看 ——
      律师签字前要找的正是那些没出处的论断。
    """
    anchors = list(session.scalars(
        select(Anchor).where(Anchor.doc_kind == doc_kind, Anchor.doc_id == doc_id)
    ).all())
    by_part: Dict[str, List[Anchor]] = {}
    for a in anchors:
        by_part.setdefault(a.part_key, []).append(a)

    cites = {c.id: c for c in sources_of(session, doc_kind, doc_id)}

    out: List[Dict[str, Any]] = []
    for key, title, text in parts:
        sents = split_sentences(text or "")
        mine = by_part.get(key, [])
        rows: List[Dict[str, Any]] = []
        for idx, (a, b, seg) in enumerate(sents):
            here = [x for x in mine if a <= x.start < b]
            ords = sorted({cites[x.citation_id].ordinal
                           for x in here if x.citation_id in cites})
            clean = CITE_RE.sub("", seg).strip()
            rows.append({
                "index": idx,
                "start": a,
                "end": b,
                "text": seg.strip(),
                "plain": clean,
                # ★ 把句子切成「文字 / 引用标记」的片段，让界面**原位**渲染上标。
                #   剥掉标记再把编号补到句末，会读成「根据，建设工程未经竣工验收…」
                #   —— 标记本来夹在「根据」和逗号之间。律师读的是这句话本身，
                #   不能为了标注方便把它读乱。
                "segments": _segments(seg),
                "citations": ords,
                # ★ 启发式，不是判定 —— 见 ASSERTION_HINTS 的说明
                "looks_legal": (not ords) and any(h in clean for h in ASSERTION_HINTS),
            })
        out.append({"key": key, "title": title, "sentences": rows})
    return out


def unsupported(
    session: Session,
    doc_kind: str,
    doc_id: int,
    parts: List[Tuple[str, str, str]],
) -> List[Dict[str, Any]]:
    """挑出「看着像法律论断、但没标依据」的句子。

    ★ 措辞要克制：这是**提示**不是**判定**。
      说成判定，律师发现几次误报就再也不看这个提示了 ——
      那时真正没依据的论断也会被一起忽略掉。
    """
    out = []
    for part in sentence_map(session, doc_kind, doc_id, parts):
        for s in part["sentences"]:
            if s["looks_legal"]:
                out.append({
                    "part_key": part["key"], "part_title": part["title"],
                    "index": s["index"], "text": s["plain"],
                })
    return out


# ================================================================ 溯源


def trace(session: Session, principal: Principal, citation_id: int) -> Dict[str, Any]:
    """一条依据的完整来路 —— 律师问「你这条哪来的」时的答案。"""
    c = session.get(Citation, citation_id)
    if c is None or c.tenant_id != principal.tenant_id:
        raise NotFound("引用不存在", code="citation.not_found")

    where: List[Dict[str, Any]] = []
    for a in session.scalars(
        select(Anchor).where(Anchor.citation_id == c.id).order_by(Anchor.id)
    ).all():
        where.append({
            "part_key": a.part_key,
            "sentence_index": a.sentence_index,
            "sentence": a.sentence_text,
        })

    return {
        "id": c.id,
        "ordinal": c.ordinal,
        "source_type": c.source_type,
        "source_id": c.source_id,
        "title": c.title,
        "snippet": c.snippet,
        "locator": c.locator,
        "effective_status": c.effective_status,
        "corpus_version": c.corpus_version,
        "trace_id": c.trace_id,
        "issue": c.issue,
        "retrieved_at": c.retrieved_at.isoformat() if c.retrieved_at else "",
        "used_in": where,
        # 时效有问题的要顶到最上面说
        "stale": ("失效" in (c.effective_status or "")
                  or "废止" in (c.effective_status or "")),
    }


def coverage(
    session: Session,
    doc_kind: str,
    doc_id: int,
    parts: List[Tuple[str, str, str]],
) -> Dict[str, Any]:
    """引用覆盖情况，给个总览。"""
    total = cited = flagged = 0
    for part in sentence_map(session, doc_kind, doc_id, parts):
        for s in part["sentences"]:
            total += 1
            if s["citations"]:
                cited += 1
            elif s["looks_legal"]:
                flagged += 1
    srcs = sources_of(session, doc_kind, doc_id)
    used = {a.citation_id for a in session.scalars(
        select(Anchor).where(Anchor.doc_kind == doc_kind,
                             Anchor.doc_id == doc_id)).all()}
    return {
        "sentences": total,
        "cited": cited,
        "flagged": flagged,
        "sources": len(srcs),
        # 给了但一次都没用上的依据 —— 不是错，但值得看一眼
        "unused": [c.ordinal for c in srcs if c.id not in used],
        "stale": [c.ordinal for c in srcs
                  if "失效" in (c.effective_status or "")
                  or "废止" in (c.effective_status or "")],
    }


# ================================================================ 引用体检


#: 体检结论
RECHECK_STATES = ("ok", "changed", "expired", "gone", "unknown")

RECHECK_LABELS = {
    "ok": "未变化",
    "changed": "原文已改动",
    "expired": "已失效",
    "gone": "库里找不到了",
    "unknown": "查不了",
}

#: 比对原文时忽略的字符 —— 排版差异不算"改动"
_NOISE = frozenset(chr(c) for c in (
    0x20, 0x9, 0xd, 0xa, 0x3000,
))


def _flat(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch not in _NOISE)


def recheck(session: Session, principal: Principal, doc_kind: str,
            doc_id: int) -> Dict[str, Any]:
    """★ 引用体检：这份文书引的东西，现在还是当时那样吗？

    为什么需要它：`citation` 存了 `source_id`、`snippet`（当时原文）、
    `effective_status`，**但在这之前没有任何一处拿它们和库比对过**。
    结果就是 —— 一份在草稿箱躺了三个月的起诉状，期间司法解释修订了，
    **系统一声不吭**，律师递上去还是老版本。

    ★ **只报告，不改。** 自动把旧法条替换成新的更危险 ——
      律师会以为还是他核过的那句，而实际上换了。

    ★ 查不动就说查不动（`unknown`），不假装"没问题"。
      检索服务挂了返回一片绿色，比不体检更糟。
    """
    rows = sources_of(session, doc_kind, doc_id)
    out = {
        "doc_kind": doc_kind, "doc_id": doc_id,
        "total": len(rows), "checked_at": _now().isoformat(),
        "corpus_version": "", "items": [],
        "ok": 0, "changed": 0, "expired": 0, "gone": 0, "unknown": 0,
    }
    if not rows:
        return out

    from kernel.container import container

    try:
        engine = container.get("retrieval")
    except Exception as exc:                             # noqa: BLE001
        logger.warning("拿不到检索服务，引用体检跳过：%s", exc)
        for c in rows:
            out["items"].append(_recheck_item(c, None, "unknown",
                                              "检索服务不可用"))
        out["unknown"] = len(rows)
        return out

    res = engine.lookup([c.source_id for c in rows if c.source_id])
    found = (res or {}).get("items") or {}
    out["corpus_version"] = (res or {}).get("corpus_version") or ""

    for c in rows:
        cur = found.get(c.source_id)
        state, note = _judge(c, cur)
        out["items"].append(_recheck_item(c, cur, state, note))
        out[state] += 1
    return out


def _judge(c: Citation, cur: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """判一条引用现在什么状况。

    ★ 判断顺序有讲究：**先看失效，再看改动**。
      一条既失效又改了内容的，要报「已失效」—— 那才是律师必须知道的。
    """
    if cur is None:
        return "unknown", "没查到结果（服务异常或标识为空）"
    if not cur.get("found"):
        return "gone", cur.get("reason") or "库里找不到了"

    now_status = str(cur.get("effective_status") or "")
    if "失效" in now_status and "失效" not in (c.effective_status or ""):
        return "expired", "当时是「{0}」，现在是「{1}」".format(
            c.effective_status or "未标注", now_status)

    if c.snippet and cur.get("snippet"):
        if _flat(c.snippet)[:400] != _flat(cur["snippet"])[:400]:
            return "changed", "库里的原文和当时存下来的不一致"

    return "ok", ""


def _recheck_item(c: Citation, cur: Optional[Dict[str, Any]],
                  state: str, note: str) -> Dict[str, Any]:
    return {
        "citation_id": c.id,
        "ordinal": c.ordinal,
        "source_id": c.source_id,
        "title": c.title,
        "state": state,
        "state_label": RECHECK_LABELS.get(state, state),
        "note": note,
        #: 当时冻结的原文 —— gone/changed 时这是唯一的退路
        "snippet_then": c.snippet,
        "snippet_now": (cur or {}).get("snippet", ""),
        "status_then": c.effective_status,
        "status_now": (cur or {}).get("effective_status", ""),
        "retrieved_at": c.retrieved_at.isoformat() if c.retrieved_at else "",
    }


def _now():
    return datetime.now(timezone.utc)

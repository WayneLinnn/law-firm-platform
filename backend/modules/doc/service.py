# -*- coding: utf-8 -*-
"""案卷文档的业务层。

三条底线，改代码前先读：

1. **权限跟着案件走。** 每一个入口都过 `case_api.assert_visible` ——
   数据范围和隔离墙已经在那儿实现好了，这里绝不另起一套。
   看不到这个案子的人，一个字节都不该拿到。

2. **版本只增不改。** 传新文件 = 新版本行，旧行原封不动。
   「我们当时递上去的是哪一版」在纠纷里是要命的问题。

3. **删除是软删。** 谁在什么时候删了什么，合规要问；物理删就答不上来。
   对象存储里的文件也一并留着 —— 存储很便宜，答不上话很贵。
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from kernel.container import container
from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import Principal
from modules.doc import extract as ex
from modules.doc.models import DEFAULT_FOLDERS, Document, DocumentVersion

logger = logging.getLogger(__name__)

#: 单个文件上限。超了要**明说**，别让浏览器传一半再失败。
MAX_UPLOAD = 100 * 1024 * 1024


def _storage():
    return container.get("storage")


def _case_writable(session: Session, principal, case_id) -> None:
    """写操作走这句：看得见 **且** 写得动。归档/终止的案子只读。"""
    if case_id:
        registry.api("case").assert_writable(session, principal, case_id)


def _case_visible(session: Session, principal: Principal, case_id: int) -> None:
    registry.api("case").assert_visible(session, principal, case_id)


def folders() -> List[str]:
    return list(DEFAULT_FOLDERS)


# ---------------------------------------------------------------- 上传


def _key(tenant_id: int, case_id: int, doc_id: int, version: int) -> str:
    """对象存储的键。**只用 ASCII。**

    文件名里的中文、空格、括号在 MinIO / OSS / S3 上的转义规则不一致，
    真实文件名放数据库里，键这边保持干净。
    """
    return "t{0}/c{1}/d{2}/v{3}".format(tenant_id, case_id, doc_id, version)


def upload(
    session: Session,
    principal: Principal,
    case_id: int,
    filename: str,
    data: bytes,
    content_type: str = "",
    title: str = "",
    folder: str = "其他",
    note: str = "",
    document_id: Optional[int] = None,
) -> Tuple[Document, DocumentVersion]:
    """上传一个文件。

    `document_id` 为空 = 新建一份文档；给了 = 给已有文档加一个新版本。
    """
    _case_writable(session, principal, case_id)
    if not data:
        raise DomainError("文件是空的", code="doc.empty_file")
    if len(data) > MAX_UPLOAD:
        raise DomainError(
            "文件 {0:.1f} MB，超过上限 {1} MB".format(
                len(data) / 1024 / 1024, MAX_UPLOAD // 1024 // 1024),
            code="doc.too_large",
        )

    if document_id is None:
        doc = Document(
            tenant_id=principal.tenant_id,
            case_id=case_id,
            folder=folder or "其他",
            title=(title or filename).strip()[:255],
            note=note,
            created_by=principal.user_id,
            current_version=0,
        )
        session.add(doc)
        session.flush()
    else:
        doc = get_document(session, principal, document_id)
        if doc.case_id != case_id:
            raise DomainError("文档不属于这个案件", code="doc.case_mismatch")

    version = doc.current_version + 1
    key = _key(principal.tenant_id, case_id, doc.id, version)

    sha = hashlib.sha256(data).hexdigest()
    # 和上一版一模一样就别浪费一个版本号 —— 律师常会重复点上传
    last = latest_version(session, doc.id)
    if last is not None and last.sha256 == sha:
        raise DomainError(
            "这个文件和当前版本（v{0}）完全相同，没有产生新版本".format(last.version),
            code="doc.duplicate",
        )

    stored = _storage().put(key, io.BytesIO(data), content_type or "application/octet-stream")

    text, status, tnote = ex.extract(data, filename, content_type)
    row = DocumentVersion(
        document_id=doc.id,
        version=version,
        storage_key=key,
        filename=filename[:255],
        content_type=content_type[:128],
        size=getattr(stored, "size", len(data)) or len(data),
        sha256=sha,
        text=text,
        text_status=status,
        text_note=tnote[:255],
        note=note,
        uploaded_by=principal.user_id,
    )
    session.add(row)
    doc.current_version = version
    session.flush()
    return doc, row


def save_text(
    session: Session,
    principal: Principal,
    case_id: int,
    title: str,
    text: str,
    folder: str = "法律意见",
    note: str = "",
    filename: str = "",
) -> Tuple[Document, DocumentVersion]:
    """把一段文字直接存成文档。

    ★ 这是给**流程**用的：Step 10 的法律意见书跑完就该落进案卷，
      而不是留在内存里等页面一关就没。
    """
    body = (text or "").strip()
    if not body:
        raise DomainError("内容是空的，不存", code="doc.empty_text")
    name = filename or (title.strip()[:60] or "文档") + ".md"
    return upload(
        session, principal, case_id, name, body.encode("utf-8"),
        content_type="text/markdown", title=title, folder=folder, note=note,
    )


# ---------------------------------------------------------------- 读


def get_document(session: Session, principal: Principal, document_id: int) -> Document:
    doc = session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == principal.tenant_id,
        )
    )
    if doc is None:
        raise NotFound("文档不存在", code="doc.not_found")
    _case_visible(session, principal, doc.case_id)
    return doc


def versions_of(session: Session, document_id: int) -> List[DocumentVersion]:
    return list(session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
    ).all())


def latest_version(session: Session, document_id: int) -> Optional[DocumentVersion]:
    return session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
        .limit(1)
    )


def get_version(
    session: Session, principal: Principal, version_id: int
) -> Tuple[Document, DocumentVersion]:
    row = session.get(DocumentVersion, version_id)
    if row is None:
        raise NotFound("版本不存在", code="doc.version_not_found")
    doc = get_document(session, principal, row.document_id)   # 顺带过权限
    return doc, row


def download(
    session: Session, principal: Principal, version_id: int
) -> Tuple[Document, DocumentVersion, bytes]:
    """取文件本体。

    ★ **走后端转发，不发对象存储的预签名链接。**
      预签名 URL 一旦发出去，在有效期内**谁拿到谁能看** —— 案卷材料受
      保密义务约束，不能让一条链接绕过权限体系。走后端还能把每次下载记进审计。
      代价是带宽，这个代价值得付。
    """
    doc, row = get_version(session, principal, version_id)
    stream = _storage().get(row.storage_key)
    data = stream.read() if hasattr(stream, "read") else bytes(stream)
    return doc, row, data


def list_documents(
    session: Session,
    principal: Principal,
    case_id: int,
    folder: str = "",
    include_deleted: bool = False,
) -> List[Document]:
    _case_visible(session, principal, case_id)
    stmt = select(Document).where(
        Document.tenant_id == principal.tenant_id,
        Document.case_id == case_id,
    )
    if folder:
        stmt = stmt.where(Document.folder == folder)
    if not include_deleted:
        stmt = stmt.where(Document.deleted.is_(False))
    return list(session.scalars(stmt.order_by(Document.folder, Document.id.desc())).all())


# ---------------------------------------------------------------- 全文检索


def search(
    session: Session,
    principal: Principal,
    keyword: str,
    case_id: Optional[int] = None,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """在案卷正文里搜。

    ★ 用 `pg_trgm` 的 ILIKE 而不是 `to_tsvector`：Postgres 自带的分词器
      **不认识中文**，`to_tsvector('simple', '建设工程施工合同')` 会把整句
      当成一个词，搜「施工合同」什么都搜不到。中文要么装 zhparser/pg_jieba
      这类扩展，要么老老实实按子串搜 —— 我们选后者，用 GIN + trgm 索引撑速度。

    ★ **权限**：结果里的每个案件都要过一遍可见性。宁可慢一点，
      也不能让人从案卷正文里读到他看不到的案子。
    """
    kw = (keyword or "").strip()
    if len(kw) < 2:
        raise DomainError("关键词至少两个字", code="doc.keyword_too_short")

    like = "%{0}%".format(kw.replace("%", r"\%").replace("_", r"\_"))
    stmt = (
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            Document.tenant_id == principal.tenant_id,
            Document.deleted.is_(False),
            DocumentVersion.version == Document.current_version,
            or_(DocumentVersion.text.ilike(like), Document.title.ilike(like)),
        )
        .order_by(Document.id.desc())
        .limit(limit * 3)          # 多取一些，权限过滤会刷掉一批
    )

    seen_cases: Dict[int, bool] = {}
    out: List[Dict[str, Any]] = []
    for doc, ver in session.execute(stmt).all():
        if case_id is not None and doc.case_id != case_id:
            continue
        ok = seen_cases.get(doc.case_id)
        if ok is None:
            try:
                _case_visible(session, principal, doc.case_id)
                ok = True
            except Exception:      # noqa: BLE001  看不到就跳过，不是错误
                ok = False
            seen_cases[doc.case_id] = ok
        if not ok:
            continue
        out.append({
            "document_id": doc.id,
            "case_id": doc.case_id,
            "title": doc.title,
            "folder": doc.folder,
            "version": ver.version,
            "version_id": ver.id,
            "filename": ver.filename,
            "text_status": ver.text_status,
            "snippet": _snippet(ver.text, kw),
        })
        if len(out) >= limit:
            break
    return out


def _snippet(text: str, keyword: str, width: int = 60) -> str:
    """命中处前后各截一段。搜到了得让人看见搜到的是哪句。"""
    if not text:
        return ""
    i = text.lower().find(keyword.lower())
    if i < 0:
        return text[:width * 2].replace("\n", " ")
    start = max(0, i - width)
    end = min(len(text), i + len(keyword) + width)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")


def unsearchable(
    session: Session, principal: Principal, case_id: int
) -> List[Dict[str, Any]]:
    """列出**搜不到的**文件（扫描件、图片、解析失败）。

    ★ 这个接口是这个模块里最容易被忽略、但最该有的东西。
      律师搜不到某份材料时，第一反应是「案卷里没有」，而不是
      「它是扫描件」。把这批文件单独列出来，才不会让人做出错误判断。
    """
    _case_visible(session, principal, case_id)
    rows = session.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            Document.tenant_id == principal.tenant_id,
            Document.case_id == case_id,
            Document.deleted.is_(False),
            DocumentVersion.version == Document.current_version,
            DocumentVersion.text_status != "ok",
        )
        .order_by(Document.id.desc())
    ).all()
    return [{
        "document_id": d.id, "title": d.title, "folder": d.folder,
        "filename": v.filename, "text_status": v.text_status, "reason": v.text_note,
    } for d, v in rows]


# ---------------------------------------------------------------- 改


def rename(session: Session, principal: Principal, document_id: int,
           title: str = "", folder: str = "", note: Optional[str] = None) -> Document:
    doc = get_document(session, principal, document_id)
    # ★ 归档后案卷只读 —— 连改个名字都不行，卷宗目录已经按这个名字编好了
    _case_writable(session, principal, doc.case_id)
    if title.strip():
        doc.title = title.strip()[:255]
    if folder.strip():
        doc.folder = folder.strip()[:64]
    if note is not None:
        doc.note = note
    session.flush()
    return doc


def soft_delete(session: Session, principal: Principal, document_id: int) -> Document:
    """软删。文件本体留在对象存储里，不动。"""
    doc = get_document(session, principal, document_id)
    # ★ 归档后案卷只读 —— 不许改名、不许删、不许恢复
    _case_writable(session, principal, doc.case_id)
    if doc.deleted:
        raise DomainError("这份文档已经删过了", code="doc.already_deleted")
    doc.deleted = True
    doc.deleted_by = principal.user_id
    doc.deleted_at = datetime.now(timezone.utc)
    session.flush()
    return doc


def restore(session: Session, principal: Principal, document_id: int) -> Document:
    doc = get_document(session, principal, document_id)
    # ★ 归档后案卷只读 —— 不许改名、不许删、不许恢复
    _case_writable(session, principal, doc.case_id)
    doc.deleted = False
    doc.deleted_by = None
    doc.deleted_at = None
    session.flush()
    return doc


def stats(session: Session, principal: Principal, case_id: int) -> Dict[str, Any]:
    _case_visible(session, principal, case_id)
    total, size = session.execute(
        select(func.count(Document.id), func.coalesce(func.sum(DocumentVersion.size), 0))
        .select_from(Document)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(
            Document.tenant_id == principal.tenant_id,
            Document.case_id == case_id,
            Document.deleted.is_(False),
            DocumentVersion.version == Document.current_version,
        )
    ).one()
    blind = len(unsearchable(session, principal, case_id))
    return {"documents": int(total or 0), "bytes": int(size or 0), "unsearchable": blind}


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的案卷材料。**按卷宗分类列**，和律所归档的习惯一致。"""
    _case_visible(session, principal, case_id)
    rows = [d for d in list_documents(session, principal, case_id)
            if not d.deleted]
    by_folder = {}
    for d in rows:
        by_folder.setdefault(d.folder or "未分类", []).append(d)
    out = []
    for folder in sorted(by_folder):
        docs = by_folder[folder]
        # ★ 扫描件在目录上要标出来 —— 归档后这卷是搜不到那部分内容的。
        #   text_status 在**版本**上不在文档上，所以要取当前版本来看。
        blind = 0
        for d in docs:
            v = latest_version(session, d.id)
            if v is not None and v.text_status != "ok":
                blind += 1
        out.append({
            "name": "案卷材料 · {0}".format(folder),
            "count": len(docs),
            "note": "其中 {0} 件搜不到正文（扫描件）".format(blind) if blind else "",
        })
    return out


def archive_blockers(session, principal, case_id):
    """归档前案卷这边的检查。"""
    _case_visible(session, principal, case_id)
    rows = [d for d in list_documents(session, principal, case_id)
            if not d.deleted]
    if not rows:
        # ★ 空卷不拦，只提醒。有的案子确实没有材料（口头咨询转的）
        return [{"what": "案卷里一份材料都没有",
                 "how": "确认材料都传了；确实没有的话可以强制归档"}]
    return []

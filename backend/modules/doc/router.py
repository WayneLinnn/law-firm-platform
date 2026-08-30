from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.doc import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    filename: str
    content_type: str
    size: int
    sha256: str
    #: ok 能搜到 / empty 多半是扫描件 / unsupported 不解析 / failed 解析出错
    text_status: str
    text_note: str
    note: str
    uploaded_by: int
    uploaded_at: datetime


class DocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    folder: str
    title: str
    note: str
    current_version: int
    deleted: bool
    created_by: int
    created_at: datetime
    updated_at: datetime


class DocDetailOut(DocOut):
    versions: List[VersionOut]


class RenameIn(BaseModel):
    title: str = ""
    folder: str = ""
    note: Optional[str] = None


class SaveTextIn(BaseModel):
    case_id: int
    title: str
    text: str
    folder: str = "法律意见"
    note: str = ""


# ---------------------------------------------------------------- 上传


@router.post("/documents", response_model=DocDetailOut)
async def upload_document(
    request: Request,
    case_id: int = Form(...),
    file: UploadFile = File(...),
    title: str = Form(""),
    folder: str = Form("其他"),
    note: str = Form(""),
    document_id: Optional[int] = Form(None),
    principal: Principal = Depends(require_perm("doc.upload")),
    session: Session = Depends(get_session),
) -> DocDetailOut:
    """上传文件。`document_id` 给了就是给已有文档加新版本。"""
    data = await file.read()
    doc, ver = service.upload(
        session, principal, case_id,
        filename=file.filename or "未命名",
        data=data,
        content_type=file.content_type or "",
        title=title, folder=folder, note=note,
        document_id=document_id,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="doc.upload", resource_type="case", resource_id=str(case_id),
                ip=client_ip(request),
                detail={"document_id": doc.id, "version": ver.version,
                        "filename": ver.filename, "size": ver.size,
                        "text_status": ver.text_status})
    return _detail(session, doc)


@router.post("/documents/text", response_model=DocDetailOut)
def save_text(
    payload: SaveTextIn,
    request: Request,
    principal: Principal = Depends(require_perm("doc.upload")),
    session: Session = Depends(get_session),
) -> DocDetailOut:
    """把一段文字存成文档（流程产出的意见书走这条）。"""
    doc, ver = service.save_text(
        session, principal, payload.case_id, payload.title, payload.text,
        folder=payload.folder, note=payload.note,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="doc.save_text", resource_type="case",
                resource_id=str(payload.case_id), ip=client_ip(request),
                detail={"document_id": doc.id, "title": payload.title})
    return _detail(session, doc)


# ---------------------------------------------------------------- 读


@router.get("/folders", response_model=List[str])
def folders(principal: Principal = Depends(require_perm("doc.view"))) -> List[str]:
    return service.folders()


@router.get("/cases/{case_id}/documents", response_model=List[DocOut])
def list_documents(
    case_id: int,
    folder: str = Query(default=""),
    include_deleted: bool = Query(default=False),
    principal: Principal = Depends(require_perm("doc.view")),
    session: Session = Depends(get_session),
) -> List[DocOut]:
    return [DocOut.model_validate(d) for d in
            service.list_documents(session, principal, case_id, folder, include_deleted)]


@router.get("/cases/{case_id}/stats")
def stats(
    case_id: int,
    principal: Principal = Depends(require_perm("doc.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.stats(session, principal, case_id)


@router.get("/cases/{case_id}/unsearchable")
def unsearchable(
    case_id: int,
    principal: Principal = Depends(require_perm("doc.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """搜不到的文件清单 —— 扫描件、图片、解析失败的。

    律师搜不着东西时会以为「案卷里没有」，这个清单告诉他「有，但搜不到」。
    """
    return service.unsearchable(session, principal, case_id)


@router.get("/documents/{document_id}", response_model=DocDetailOut)
def get_document(
    document_id: int,
    principal: Principal = Depends(require_perm("doc.view")),
    session: Session = Depends(get_session),
) -> DocDetailOut:
    return _detail(session, service.get_document(session, principal, document_id))


@router.get("/search")
def search(
    q: str = Query(description="关键词，至少两个字"),
    case_id: Optional[int] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    principal: Principal = Depends(require_perm("doc.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    return service.search(session, principal, q, case_id, limit)


@router.get("/versions/{version_id}/download")
def download(
    version_id: int,
    request: Request,
    principal: Principal = Depends(require_perm("doc.view")),
    session: Session = Depends(get_session),
) -> Response:
    """下载文件本体。

    ★ **走后端转发，不发对象存储的预签名链接。**
      预签名 URL 发出去之后，在有效期内谁拿到谁能看 —— 案卷材料受保密义务
      约束，不能让一条链接绕过权限体系。走后端还能把每次下载记进审计。
    """
    doc, ver, data = service.download(session, principal, version_id)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="doc.download", resource_type="case", resource_id=str(doc.case_id),
                ip=client_ip(request),
                detail={"document_id": doc.id, "version": ver.version,
                        "filename": ver.filename})
    # 中文文件名要按 RFC 5987 编码，否则浏览器那边会变成乱码
    quoted = urllib.parse.quote(ver.filename)
    return Response(
        content=data,
        media_type=ver.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''{0}".format(quoted),
            "Content-Length": str(len(data)),
        },
    )


# ---------------------------------------------------------------- 改


@router.patch("/documents/{document_id}", response_model=DocOut)
def rename(
    document_id: int,
    payload: RenameIn,
    principal: Principal = Depends(require_perm("doc.manage")),
    session: Session = Depends(get_session),
) -> DocOut:
    return DocOut.model_validate(service.rename(
        session, principal, document_id, payload.title, payload.folder, payload.note))


@router.post("/documents/{document_id}/delete", response_model=DocOut)
def soft_delete(
    document_id: int,
    request: Request,
    principal: Principal = Depends(require_perm("doc.delete")),
    session: Session = Depends(get_session),
) -> DocOut:
    """软删。**文件本体不动** —— 谁在什么时候删了什么，合规要问。"""
    doc = service.soft_delete(session, principal, document_id)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="doc.delete", resource_type="case", resource_id=str(doc.case_id),
                ip=client_ip(request), detail={"document_id": doc.id, "title": doc.title})
    return DocOut.model_validate(doc)


@router.post("/documents/{document_id}/restore", response_model=DocOut)
def restore(
    document_id: int,
    principal: Principal = Depends(require_perm("doc.delete")),
    session: Session = Depends(get_session),
) -> DocOut:
    return DocOut.model_validate(service.restore(session, principal, document_id))


# ---------------------------------------------------------------- 内部


def _detail(session: Session, doc) -> DocDetailOut:
    return DocDetailOut(
        **DocOut.model_validate(doc).model_dump(),
        versions=[VersionOut.model_validate(v)
                  for v in service.versions_of(session, doc.id)],
    )


DocDetailOut.model_rebuild()

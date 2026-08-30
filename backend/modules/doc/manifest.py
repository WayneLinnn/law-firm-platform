"""案卷文档。文件本体进对象存储，库里只留元数据与正文。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class SearchParams(BaseModel):
    keyword: str = Field(description="在案卷正文里搜什么，至少两个字")
    case_id: int = Field(default=0, description="限定案件，0 表示全部可见案件")


class CaseParams(BaseModel):
    case_id: int = Field(description="案件 id")


MANIFEST = ModuleManifest(
    name="doc",
    version="0.1.0",
    description="案卷文档：上传、卷宗分类、版本、全文检索",
    #: 文档必须挂在案件下 —— 不挂案件就没法判权限
    requires=("identity", "case"),
    optional=(),
    permissions=(
        "doc.view",      # 看案卷、下载
        "doc.upload",    # 上传、加版本
        "doc.manage",    # 改名、改卷宗、写备注
        "doc.delete",    # 删除（软删）
    ),
    capabilities=(
        Capability(
            name="doc.search",
            description="在案卷正文里全文检索",
            params=SearchParams,
            requires_perm="doc.view",
            read_only=True,
        ),
        Capability(
            name="doc.list",
            description="列出某个案件的案卷材料",
            params=CaseParams,
            requires_perm="doc.view",
            read_only=True,
        ),
    ),
)

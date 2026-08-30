"""draft 模块的对外唯一出口。"""
from modules.draft.service import (
    archive_inventory,
    archive_blockers,
    assemble,
    citations_of,
    create,
    drafts_of_case,
    edit_section,
    finalize,
    generate_section,
    get_draft,
    parts_of,
    readiness,
    sections_of,
    verify_citations,
    void,
)
from modules.draft.templates import describe as describe_templates

__all__ = [
    # 归档：卷宗目录 / 归档前的阻断检查（archive 模块来问）
    "archive_inventory", "archive_blockers",
    "create", "generate_section", "edit_section", "assemble",
    "readiness", "finalize", "void",
    "get_draft", "sections_of", "parts_of", "drafts_of_case", "citations_of",
    "verify_citations", "describe_templates",
]

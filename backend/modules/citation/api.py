"""citation 模块的对外唯一出口。

产出文书的模块（draft、将来的 workflow）通过这里挂引用：

    citation.attach(...)    把可引用的依据快照进来
    citation.reanchor(...)  正文一变就重算锚点
"""
from modules.citation.service import (
    RECHECK_LABELS,
    recheck,
    attach,
    clear,
    coverage,
    reanchor,
    sentence_map,
    sources_of,
    split_sentences,
    trace,
    unsupported,
)

__all__ = [
    "attach", "reanchor", "clear",
    "sources_of", "sentence_map", "unsupported", "coverage", "trace",
    "split_sentences",
    # ★ 引用体检：法条改了/废止了，这里能查出来
    "recheck", "RECHECK_LABELS",
]

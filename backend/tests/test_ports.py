"""契约层：只验证接口存在与形状，不涉及实现（实现在 Step 2 / Step 8）。"""
from kernel import ports


def test_all_ports_exported():
    for name in ("ModelPort", "RetrievalPort", "LegalQAPort",
                 "WorkflowPort", "StoragePort", "EventPort"):
        assert hasattr(ports, name), name


def test_retrieval_and_qa_are_separate_ports():
    """C2：纯检索与问答刻意拆开 —— 计费/缓存/重试策略完全不同。"""
    assert ports.RetrievalPort is not ports.LegalQAPort
    assert hasattr(ports.RetrievalPort, "search")
    # 问答一律任务化（C3：要素抽取实测 43 秒，不能同步阻塞）
    for method in ("submit_answer", "submit_extract", "submit_compare", "poll", "result"):
        assert hasattr(ports.LegalQAPort, method), method


def test_citation_is_structured():
    """引用必须结构化，只给总结文字后面全废。"""
    c = ports.Citation(source_type="law", source_id="1", title="t", snippet="原文片段")
    for field_name in ("source_type", "source_id", "title", "snippet",
                       "url", "locator", "effective_status"):
        assert hasattr(c, field_name), field_name
